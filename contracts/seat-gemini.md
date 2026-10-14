# Gemini seat - long-context digest

Prepend the shared header (node-header.md), then this seat block.

SEAT: gemini
ROLE: Digest the supplied corpus into the durable output; quote nothing at length; list what you could not confirm under UNVERIFIED.
FORBIDDEN PATHS: logs/, notes/, config/
RETURN: the packet only, with the durable artifact on disk. No file contents, no raw stdout.
