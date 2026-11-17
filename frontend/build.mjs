import {build} from 'esbuild';
import {mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import {relative, resolve, sep} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('.', import.meta.url));
const outdir = resolve(root, '../src/genesis_curation/web/assets');
await rm(outdir, {recursive: true, force: true});
await mkdir(outdir, {recursive: true});
const result = await build({
  absWorkingDir: root,
  entryPoints: ['src/main.tsx'],
  outdir,
  entryNames: 'assets/[name]-[hash]',
  assetNames: 'assets/[name]-[hash]',
  bundle: true,
  minify: true,
  format: 'esm',
  target: 'es2022',
  jsx: 'automatic',
  define: {'process.env.NODE_ENV': JSON.stringify('production')},
  legalComments: 'eof',
  metafile: true,
  logLevel: 'info',
});
const entry = Object.entries(result.metafile.outputs).find(([file, info]) =>
  file.endsWith('.js') && info.entryPoint);
if (!entry || !entry[1].cssBundle) throw new Error('Missing application JS or CSS bundle');
const assetURL = file => '/' + relative(outdir, resolve(root, file)).split(sep).join('/');
const tags = `<link rel="stylesheet" href="${assetURL(entry[1].cssBundle)}"/>` +
  `<script type="module" src="${assetURL(entry[0])}"></script>`;
const html = await readFile(resolve(root, 'index.html'), 'utf8');
if (!html.includes('<!-- APP_ASSETS -->')) throw new Error('Missing HTML asset marker');
await writeFile(resolve(outdir, 'index.html'), html.replace('<!-- APP_ASSETS -->', tags));
