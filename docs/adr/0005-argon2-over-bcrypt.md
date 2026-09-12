# ADR 0005 — Argon2id for password hashing

- **Status:** Accepted
- **Date:** 2026-09-12
- **Milestone:** M1

## Context

Passwords must be stored as slow, salted one-way hashes. The conventional
Python choice is `passlib[bcrypt]`, which appears in most FastAPI tutorials.

## Decision

Use `argon2-cffi` directly, with Argon2id at `time_cost=3`, `memory_cost=64 MiB`,
`parallelism=4`.

## Rationale

**Algorithm.** Argon2id won the Password Hashing Competition (2015) and is the
first recommendation in the OWASP Password Storage Cheat Sheet. Unlike bcrypt it
is *memory-hard*: cracking requires 64 MiB per guess, which removes most of the
advantage a GPU or ASIC farm has over a CPU. bcrypt remains acceptable; Argon2id
is simply better, at equal implementation effort.

**Library.** `passlib` has had no feature release since 2020 and its bcrypt
backend misreads the version of bcrypt 4.x, producing a spurious error on
import that is a well-known source of confusion on Windows. `argon2-cffi` is
maintained, has a three-function API, and has prebuilt wheels for Python 3.12
on Windows.

## Implementation notes

- `verify_password()` catches `VerifyMismatchError`, `VerificationError`, and
  `InvalidHashError`, returning `False` for all three. Callers get a boolean and
  never have to reason about exception types; a corrupted hash in the database
  fails closed rather than raising a 500.
- `needs_rehash()` is checked on every successful login. If the cost parameters
  are strengthened later, existing users are transparently upgraded the next
  time they sign in, without a password reset.
- Login returns an identical error for an unknown email and a wrong password, so
  the endpoint cannot be used to discover which addresses have accounts.
- The registration schema caps password length at 128 characters. Argon2 is
  deliberately expensive, so unbounded input is a cost-amplification vector.

## Consequences

**Advantages**
- Current best-practice hashing; defensible under viva questioning.
- One small maintained dependency instead of a large unmaintained one.
- Cost parameters can be raised later without a migration.

**Limitations**
- Each hash costs ~64 MiB and tens of milliseconds. Fine at this scale, but it
  is genuine server load — which is why the login endpoint would need rate
  limiting before any public deployment. Noted as future work.
- Argon2 hashes are ~95 characters; the `password_hash` column is sized at 255
  to leave room for stronger future parameters.
