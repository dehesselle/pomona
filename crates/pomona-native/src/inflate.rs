// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! A deflate stream that lasts the whole session, as RFB's zlib and ZRLE keep
//! one: each rectangle is a piece of it, flushed at its end and inflated on the
//! window the ones before it left.

use flate2::{Decompress, FlushDecompress, Status};

/// How much output room to add at a time, at least.
const STEP: usize = 64 * 1024;

pub struct Stream {
    inflater: Decompress,
    out: Vec<u8>,
}

impl Default for Stream {
    fn default() -> Self {
        Self::new()
    }
}

impl Stream {
    pub fn new() -> Self {
        Self {
            inflater: Decompress::new(true),
            out: Vec::new(),
        }
    }

    /// Inflate all of `input`, one rectangle's piece of the stream, and return
    /// what it held. `expected` sizes the output up front where it is known.
    pub fn inflate(&mut self, input: &[u8], expected: usize) -> Result<&[u8], String> {
        self.out.clear();
        self.out.reserve(expected.max(STEP));
        let mut consumed = 0;
        loop {
            if self.out.capacity() - self.out.len() < STEP {
                self.out.reserve(self.out.capacity().max(STEP));
            }
            let (before_in, before_out) = (self.inflater.total_in(), self.inflater.total_out());
            let status = self
                .inflater
                .decompress_vec(&input[consumed..], &mut self.out, FlushDecompress::Sync)
                .map_err(|error| format!("the deflate stream broke: {error}"))?;
            let read = (self.inflater.total_in() - before_in) as usize;
            let written = (self.inflater.total_out() - before_out) as usize;
            consumed += read;
            // Done once every byte is in and the inflater stopped short of
            // filling the room it had: nothing more is held back.
            if consumed == input.len() && self.out.len() < self.out.capacity() {
                break;
            }
            if status == Status::StreamEnd {
                return Err("the deflate stream ended, which the Mac never does".to_owned());
            }
            if read == 0 && written == 0 && self.out.len() < self.out.capacity() {
                return Err("the deflate stream stopped making progress".to_owned());
            }
        }
        Ok(&self.out)
    }
}

#[cfg(test)]
pub mod tests {
    use flate2::{Compress, Compression, FlushCompress};

    use super::*;

    /// Deflate `pieces` as one stream, flushed after each, as the Mac does.
    pub fn deflate_pieces(pieces: &[&[u8]]) -> Vec<Vec<u8>> {
        let mut compress = Compress::new(Compression::default(), true);
        pieces
            .iter()
            .map(|piece| {
                let mut out = Vec::with_capacity(piece.len() * 2 + 1024);
                let start = compress.total_in();
                loop {
                    let done = (compress.total_in() - start) as usize;
                    compress
                        .compress_vec(&piece[done..], &mut out, FlushCompress::Sync)
                        .unwrap();
                    if (compress.total_in() - start) as usize == piece.len() && out.len() < out.capacity() {
                        break;
                    }
                    out.reserve(out.capacity());
                }
                out
            })
            .collect()
    }

    #[test]
    fn pieces_inflate_in_turn_on_one_stream() {
        let first: Vec<u8> = (0..200_000u32).map(|i| (i % 251) as u8).collect();
        let second = b"the window carries over".repeat(100);
        let deflated = deflate_pieces(&[&first, &second]);
        let mut stream = Stream::new();
        assert_eq!(stream.inflate(&deflated[0], 0).unwrap(), &first[..]);
        assert_eq!(stream.inflate(&deflated[1], second.len()).unwrap(), &second[..]);
    }

    #[test]
    fn a_piece_alone_does_not_inflate_without_the_ones_before() {
        let deflated = deflate_pieces(&[b"aaaa", b"bbbb"]);
        // A fresh stream misses the zlib header that only the first piece has.
        assert!(Stream::new().inflate(&deflated[1], 4).is_err());
    }
}
