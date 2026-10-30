// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! Showing the canvas in a window with wgpu: one texture holding the
//! framebuffer, the changed rectangles written into it, and one triangle that
//! covers the window and samples it, letterboxed to keep the aspect.

use std::sync::{Arc, Mutex};

use winit::window::Window;

use crate::canvas::{Canvas, Damage};

const SHADER: &str = r"
struct Out {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
}

@vertex
fn vs(@builtin(vertex_index) index: u32) -> Out {
    // One triangle, larger than the viewport, whose middle covers it.
    let uv = vec2<f32>(f32((index << 1u) & 2u), f32(index & 2u));
    var out: Out;
    out.position = vec4<f32>(uv * vec2<f32>(2.0, -2.0) + vec2<f32>(-1.0, 1.0), 0.0, 1.0);
    out.uv = uv;
    return out;
}

@group(0) @binding(0) var picture: texture_2d<f32>;
@group(0) @binding(1) var picture_sampler: sampler;

@fragment
fn fs(in: Out) -> @location(0) vec4<f32> {
    return textureSample(picture, picture_sampler, in.uv);
}
";

/// Where the letterbox around the picture is: a dark grey.
const BACKGROUND: wgpu::Color = wgpu::Color {
    r: 0.1,
    g: 0.1,
    b: 0.1,
    a: 1.0,
};

/// What came of drawing a frame.
pub enum Frame {
    Presented,
    /// Nothing to draw to: the window is hidden, or the surface timed out.
    Skipped,
    /// The surface was set up again; ask for the frame once more.
    Again,
}

pub struct Gpu {
    pub window: Arc<Window>,
    surface: wgpu::Surface<'static>,
    device: wgpu::Device,
    queue: wgpu::Queue,
    config: wgpu::SurfaceConfiguration,
    pipeline: wgpu::RenderPipeline,
    layout: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
    texture_format: wgpu::TextureFormat,
    picture: Option<Picture>,
    /// Which adapter draws, for the log.
    pub adapter: String,
    /// The first error wgpu reported, which would otherwise panic.
    errors: Arc<Mutex<Option<String>>>,
}

struct Picture {
    texture: wgpu::Texture,
    bind_group: wgpu::BindGroup,
    width: u32,
    height: u32,
}

impl Gpu {
    pub fn new(window: Arc<Window>) -> Result<Self, String> {
        let instance = wgpu::Instance::new(wgpu::InstanceDescriptor {
            backends: wgpu::Backends::DX12 | wgpu::Backends::METAL,
            ..wgpu::InstanceDescriptor::new_without_display_handle()
        });
        let surface = instance
            .create_surface(window.clone())
            .map_err(|error| format!("no surface for the window: {error}"))?;
        // A machine without a GPU, such as a virtual one, still has the
        // system's software adapter (WARP on Windows).
        let adapter = [false, true]
            .into_iter()
            .find_map(|fallback| {
                pollster::block_on(instance.request_adapter(&wgpu::RequestAdapterOptions {
                    power_preference: wgpu::PowerPreference::LowPower,
                    force_fallback_adapter: fallback,
                    compatible_surface: Some(&surface),
                    ..Default::default()
                }))
                .ok()
            })
            .ok_or("no graphics adapter can draw to the window")?;
        let info = adapter.get_info();
        let adapter_name = format!("{} ({:?}, {:?})", info.name, info.backend, info.device_type);
        let (device, queue) = pollster::block_on(adapter.request_device(&wgpu::DeviceDescriptor {
            label: Some("pomona"),
            ..Default::default()
        }))
        .map_err(|error| format!("no graphics device: {error}"))?;
        let errors = Arc::new(Mutex::new(None));
        let sink = errors.clone();
        device.on_uncaptured_error(Arc::new(move |error: wgpu::Error| {
            sink.lock().unwrap_or_else(|p| p.into_inner()).get_or_insert(error.to_string());
        }));

        let capabilities = surface.get_capabilities(&adapter);
        // A plain format if there is one, and a texture to match: either way
        // the Mac's bytes reach the screen as they are.
        let format = capabilities
            .formats
            .iter()
            .copied()
            .find(|format| !format.is_srgb())
            .or_else(|| capabilities.formats.first().copied())
            .ok_or("the window's surface offers no format")?;
        let texture_format = if format.is_srgb() {
            wgpu::TextureFormat::Rgba8UnormSrgb
        } else {
            wgpu::TextureFormat::Rgba8Unorm
        };
        let size = window.inner_size();
        let config = wgpu::SurfaceConfiguration {
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
            format,
            color_space: wgpu::SurfaceColorSpace::Auto,
            width: size.width.max(1),
            height: size.height.max(1),
            present_mode: wgpu::PresentMode::AutoVsync,
            desired_maximum_frame_latency: 1,
            alpha_mode: capabilities.alpha_modes[0],
            view_formats: Vec::new(),
        };
        surface.configure(&device, &config);
        if let Some(error) = errors.lock().unwrap_or_else(|p| p.into_inner()).take() {
            return Err(format!("the window cannot be drawn to: {error}"));
        }

        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("picture"),
            source: wgpu::ShaderSource::Wgsl(SHADER.into()),
        });
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("picture"),
            entries: &[
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
            ],
        });
        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("picture"),
            bind_group_layouts: &[Some(&layout)],
            immediate_size: 0,
        });
        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("picture"),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs"),
                compilation_options: Default::default(),
                buffers: &[],
            },
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs"),
                compilation_options: Default::default(),
                targets: &[Some(wgpu::ColorTargetState {
                    format,
                    blend: None,
                    write_mask: wgpu::ColorWrites::ALL,
                })],
            }),
            multiview_mask: None,
            cache: None,
        });
        // Linear: exact at one to one, smooth when the window scales.
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("picture"),
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });
        if let Some(error) = errors.lock().unwrap_or_else(|p| p.into_inner()).take() {
            return Err(format!("the picture cannot be drawn: {error}"));
        }
        Ok(Self {
            window,
            surface,
            device,
            queue,
            config,
            pipeline,
            layout,
            sampler,
            texture_format,
            picture: None,
            adapter: adapter_name,
            errors,
        })
    }

    pub fn resize(&mut self, width: u32, height: u32) {
        self.config.width = width.max(1);
        self.config.height = height.max(1);
        self.surface.configure(&self.device, &self.config);
    }

    /// Write what changed on the canvas into the texture: everything after a
    /// resize, or when there is no texture yet; the changed rectangles
    /// otherwise.
    pub fn upload(&mut self, canvas: &Canvas, damage: &Damage) {
        let (width, height) = (canvas.width() as u32, canvas.height() as u32);
        if width == 0 || height == 0 {
            self.picture = None;
            return;
        }
        let whole = damage.resized
            || self
                .picture
                .as_ref()
                .is_none_or(|p| (p.width, p.height) != (width, height));
        if whole {
            self.picture = Some(self.new_picture(width, height));
        }
        let Some(picture) = &self.picture else {
            return;
        };
        let full = [(0, 0, width as usize, height as usize)];
        let rects = if whole { &full[..] } else { &damage.rects[..] };
        let bytes: &[u8] = bytemuck::cast_slice(canvas.pixels());
        for &(x, y, w, h) in rects {
            self.queue.write_texture(
                wgpu::TexelCopyTextureInfo {
                    texture: &picture.texture,
                    mip_level: 0,
                    origin: wgpu::Origin3d {
                        x: x as u32,
                        y: y as u32,
                        z: 0,
                    },
                    aspect: wgpu::TextureAspect::All,
                },
                bytes,
                wgpu::TexelCopyBufferLayout {
                    offset: ((y * width as usize + x) * 4) as u64,
                    bytes_per_row: Some(width * 4),
                    rows_per_image: None,
                },
                wgpu::Extent3d {
                    width: w as u32,
                    height: h as u32,
                    depth_or_array_layers: 1,
                },
            );
        }
    }

    fn new_picture(&self, width: u32, height: u32) -> Picture {
        let texture = self.device.create_texture(&wgpu::TextureDescriptor {
            label: Some("picture"),
            size: wgpu::Extent3d {
                width,
                height,
                depth_or_array_layers: 1,
            },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: self.texture_format,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
        let bind_group = self.device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("picture"),
            layout: &self.layout,
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: wgpu::BindingResource::TextureView(&view),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: wgpu::BindingResource::Sampler(&self.sampler),
                },
            ],
        });
        Picture {
            texture,
            bind_group,
            width,
            height,
        }
    }

    /// Where the picture goes in the window: as large as fits, centred, on
    /// whole pixels.
    pub fn viewport(&self) -> Option<(f32, f32, f32, f32)> {
        let picture = self.picture.as_ref()?;
        let (sw, sh) = (self.config.width as f32, self.config.height as f32);
        let (pw, ph) = (picture.width as f32, picture.height as f32);
        let scale = (sw / pw).min(sh / ph);
        let (w, h) = ((pw * scale).round(), (ph * scale).round());
        Some((((sw - w) / 2.0).floor(), ((sh - h) / 2.0).floor(), w, h))
    }

    /// Draw a frame.
    pub fn render(&mut self) -> Result<Frame, String> {
        let frame = match self.surface.get_current_texture() {
            wgpu::CurrentSurfaceTexture::Success(frame) | wgpu::CurrentSurfaceTexture::Suboptimal(frame) => frame,
            wgpu::CurrentSurfaceTexture::Outdated | wgpu::CurrentSurfaceTexture::Lost => {
                self.surface.configure(&self.device, &self.config);
                return Ok(Frame::Again);
            }
            wgpu::CurrentSurfaceTexture::Timeout | wgpu::CurrentSurfaceTexture::Occluded => return Ok(Frame::Skipped),
            wgpu::CurrentSurfaceTexture::Validation => return Err("the window's surface failed validation".into()),
        };
        let view = frame.texture.create_view(&wgpu::TextureViewDescriptor::default());
        let mut encoder = self
            .device
            .create_command_encoder(&wgpu::CommandEncoderDescriptor { label: Some("frame") });
        {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("frame"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load: wgpu::LoadOp::Clear(BACKGROUND),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                ..Default::default()
            });
            if let (Some(picture), Some((x, y, w, h))) = (&self.picture, self.viewport()) {
                pass.set_viewport(x, y, w, h, 0.0, 1.0);
                pass.set_pipeline(&self.pipeline);
                pass.set_bind_group(0, &picture.bind_group, &[]);
                pass.draw(0..3, 0..1);
            }
        }
        self.queue.submit([encoder.finish()]);
        self.window.pre_present_notify();
        self.queue.present(frame);
        if let Some(error) = self.errors.lock().unwrap_or_else(|p| p.into_inner()).take() {
            return Err(format!("drawing failed: {error}"));
        }
        Ok(Frame::Presented)
    }
}
