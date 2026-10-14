# Vendored third-party code

| file | project | version | licence | sha256 |
|---|---|---|---|---|
| `elk.bundled.js` | elkjs (Eclipse Layout Kernel, JS build) https://github.com/kieler/elkjs | 0.12.0 (npm `elkjs@0.12.0`, `lib/elk.bundled.js`) | EPL-2.0 (the package is dual-licensed `EPL-2.0 OR GPL-3.0-or-later`; we use it under EPL-2.0) | `1222e44f953ce7746af23801e723708f8e6f436b8b377a6a5fc7552f34a307b3` |

The licence text is `elkjs.LICENSE.md` (EPL-2.0) next to this file. `elk.bundled.js` is UNMODIFIED: do not edit it; to upgrade, replace it with a newer
`npm pack elkjs` copy and update this table. Our own code that calls it (`canvas/js/autolayout.js`) is a separate module. Source: the elkjs package and
https://github.com/eclipse/elk. Use here is internal and loopback-only; if the canvas is ever distributed, ship this folder with it. Not legal advice.
