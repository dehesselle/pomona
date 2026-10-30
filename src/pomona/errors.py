# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""The exceptions Pomona raises."""


class PomonaError(Exception):
    """Base of every error Pomona raises on purpose."""


class ProtocolError(PomonaError):
    """The Mac sent something Pomona cannot read."""
