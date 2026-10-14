# Pinned ExVer runtime

`exver/index.js`, `exver/exver.js`, and their MIT license are copied unchanged
from the bundled `@start9labs/start-core` in the npm distribution
`@start9labs/start-sdk@3.0.6`. The two bundled runtime dependencies are
`deep-equality-data-structures` and `object-hash`; their package metadata and
licenses are retained. Source maps, declarations, and tests are omitted.

Source: https://github.com/Start9Labs/start-technologies
Distribution: https://registry.npmjs.org/@start9labs/start-sdk/-/start-sdk-3.0.6.tgz

No npm install or compilation happens on the registry node. The Python monitor
calls this runtime through `../exver.cjs`. Do not substitute ordinary SemVer:
StartOS flavors, downstream revisions, and range syntax have different rules.
