# Parked legacy code

This folder holds code that is **not run** by the live application. It is kept
for reference only, in the same relative layout it had before parking:

- Standalone entry points that the unified application replaced.
- Route modules that were never registered by the application.
- A deprecated multi-tenant database layer. Live modules still import its old
  location, which is now a compatibility shim that loads the parked file, so
  imports keep working while the code itself lives here.

Do not add new features here. New work belongs in the live application
packages. If a parked file is ever needed again, move it back deliberately,
with tests, instead of importing from this folder in new code.
