# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""The exceptions Pomona raises."""


class PomonaError(Exception):
    """Base of every error Pomona raises on purpose."""


class ProtocolError(PomonaError):
    """The Mac sent something Pomona cannot read."""


class AuthenticationError(PomonaError):
    """The Mac refused the sign-in.

    It says no more than that: a wrong password and an account without
    Remote Management permission look the same.
    """


class EndedAtMacError(PomonaError):
    """The Mac's user ended the session."""
