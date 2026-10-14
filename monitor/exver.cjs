// Use Start9's implementation, including flavor and migration-range semantics.
const { ExtendedVersion, VersionRange, Version } = require('./vendor/exver');
const fs = require('node:fs');
try {
  const q = JSON.parse(fs.readFileSync(0, 'utf8'));
  let result;
  if (q.op === 'sort') {
    result = q.versions.map(v => [v, ExtendedVersion.parse(v)])
      .sort((a, b) => a[1].compareForSort(b[1])).map(v => v[0]);
  } else if (q.op === 'matches') {
    result = q.checks.map(([v, range]) => ExtendedVersion.parse(v).satisfies(VersionRange.parse(range)));
  } else if (q.op === 'upstreamCompare') {
    result = Version.parse(q.a).compareForSort(Version.parse(q.b));
  } else if (q.op === 'parts') {
    result = q.versions.map(v => {
      const x = ExtendedVersion.parse(v);
      return { flavor: x.flavor, upstream: x.upstream.toString(), downstream: x.downstream.toString() };
    });
  } else throw new Error('Unknown operation');
  process.stdout.write(JSON.stringify(result));
} catch (e) {
  process.stderr.write(String(e.message));
  process.exitCode = 1;
}
