// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! `pomona._native`: the parts of Pomona that are too slow in Python.

pub mod canvas;
mod picture;

use pyo3::prelude::*;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<picture::Picture>()?;
    m.add("ADAPTIVE", canvas::ADAPTIVE)?;
    Ok(())
}
