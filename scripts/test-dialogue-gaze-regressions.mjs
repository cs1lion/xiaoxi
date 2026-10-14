/* Run existing browser suites sequentially and keep this run's evidence separate
 * from their historical default output directories. Original suites stay intact. */
import { readFile, writeFile, mkdir, unlink } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';

const output = `eval/avatar/dialogue-gaze-regressions/${new Date().toISOString().replace(/[:.]/g,'-')}`;
const hash = value => createHash('sha256').update(value).digest('hex');
await mkdir(output,{recursive:true});
const results=[];
for (const [name,file,hasOutput] of [
  ['eyes','scripts/test-eye-browser.mjs',true],
  ['calibration','scripts/test-calibration-browser.mjs',true],
  ['lids','scripts/test-eye-lids-browser.mjs',true],
  ['voice','scripts/test-voice-browser.mjs',false],
]) {
  const source=await readFile(file,'utf8'), temp=`scripts/.dialogue-regression-${process.pid}-${name}.mjs`;
  const rewritten=hasOutput ? source.replace(/^const output = .*;$/m,
    `const output = '${output}/${name}';`) : source;
  if (hasOutput && rewritten===source) throw new Error('Cannot isolate output for '+file);
  await writeFile(temp,rewritten);
  await writeFile(`${output}/${name}-source.mjs`,rewritten);
  const startedAt=new Date().toISOString();
  let result;
  try {
    result=await new Promise((resolve,reject)=>{
      const child=spawn(process.execPath,[temp],{stdio:['ignore','pipe','pipe']});
      let stdout='',stderr='';
      child.stdout.on('data',chunk=>stdout+=chunk);
      child.stderr.on('data',chunk=>stderr+=chunk);
      child.once('error',reject);
      child.once('close',code=>resolve({code,stdout,stderr}));
    });
  } finally { await unlink(temp); }
  const unchanged=hash(await readFile(file,'utf8'))===hash(source);
  const entry={name,file,startedAt,finishedAt:new Date().toISOString(),
    sourceSha256:hash(source),executedSha256:hash(rewritten),sourceUnchanged:unchanged,
    output:hasOutput ? output+'/'+name : null,exitCode:result.code};
  await writeFile(`${output}/${name}.log`,result.stdout+result.stderr);
  results.push(entry);
  await writeFile(output+'/results.json',JSON.stringify({results},null,2));
  console.log(JSON.stringify(entry));
  if (result.code!==0 || !unchanged) throw new Error(name+' regression failed: '+result.stderr);
}
console.log(JSON.stringify({output,passed:results.length}));
