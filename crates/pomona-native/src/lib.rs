// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! `pomona._native`: the parts of Pomona that are too slow in Python.

pub mod canvas;
pub mod inflate;
mod picture;
pub mod zrle;

use pyo3::prelude::*;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<picture::Picture>()?;
    m.add("StreamError", m.py().get_type::<picture::StreamError>())?;
    m.add("RAW", canvas::RAW)?;
    m.add("ZLIB", canvas::ZLIB)?;
    m.add("ZRLE", canvas::ZRLE)?;
    m.add("ADAPTIVE", canvas::ADAPTIVE)?;
    Ok(())
}
