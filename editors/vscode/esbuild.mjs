// Bundles the extension (and, with --tests, the Node tests) with esbuild.
import * as esbuild from "esbuild";
import { readdirSync } from "node:fs";

const production = process.argv.includes("--production");
const watch = process.argv.includes("--watch");
const tests = process.argv.includes("--tests");

const common = {
  bundle: true,
  platform: "node",
  target: "node18",
  format: "cjs",
  sourcemap: !production,
  minify: production,
  logLevel: "info",
};

if (tests) {
  const entryPoints = readdirSync("test").filter((f) => f.endsWith(".test.ts")).map((f) => `test/${f}`);
  await esbuild.build({ ...common, entryPoints, outdir: "out/test", external: ["vscode"] });
} else {
  const ctx = await esbuild.context({
    ...common,
    entryPoints: ["src/extension.ts"],
    outfile: "dist/extension.js",
    external: ["vscode"],
  });
  if (watch) {
    await ctx.watch();
  } else {
    await ctx.rebuild();
    await ctx.dispose();
  }
}
