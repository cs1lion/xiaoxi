// Deterministic synthetic acoustic stress probe, not a speech/AEC benchmark.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
const {DuplexGate}=await import(`data:text/javascript;base64,${Buffer.from(await readFile(new URL('../web/voice/duplex.js',import.meta.url))).toString('base64')}`);
const rate=16000, seconds=4, size=320, N=rate*seconds;
function signal(seed,scale){let low=0;return Float32Array.from({length:N},(_,i)=>{seed=(1664525*seed+1013904223)>>>0;low=.82*low+.18*(seed/4294967296*2-1);return low*scale*(.65+.35*Math.sin(i/rate*5)**2);});}
const scenes=[
 {name:'linear_echo',delay:640},
 {name:'fractional_delay_echo',delay:647},
 {name:'echo_plus_noise',delay:640,noise:.045},
 {name:'room_two_paths',delay:640,taps:[[240,.55],[1100,.3]]},
 {name:'speaker_clipping',delay:640,clip:.018},
 {name:'delay_switch',delay:640,switchDelay:1800},
 {name:'outside_search_260ms',delay:4160},
 {name:'no_reference_echo',delay:640,noReference:true},
 {name:'steady_noise_no_user',silent:true,noise:.3},
 {name:'double_talk',delay:640,near:.34},
 {name:'quiet_double_talk',delay:640,near:.07},
 {name:'double_talk_in_room',delay:640,taps:[[240,.55],[1100,.3]],near:.34},
];
const rows=[];
for(const mode of ['headset','speaker','immediate'])for(const scene of scenes){
 let falseTrials=0,missTrials=0,earlyTrials=0;const detection=[],suppression=[];
 for(let trial=0;trial<5;trial++){
  const far=signal(9217+trial*37,.7),noise=signal(917+trial*57,scene.noise||0),near=signal(773+trial*71,scene.near||0);
  const gate=new DuplexGate();let first=null,blocked=0,active=0;const startNear=1600;
  for(let n=0;n<N/size;n++){
   const mic=Float32Array.from({length:size},(_,j)=>{
    const i=n*size+j;let delay=scene.delay||0;if(scene.switchDelay&&i>=rate*2)delay=scene.switchDelay;
    let e=scene.silent?0:.65*(far[i-delay]||0);
    for(const [extra,gain] of scene.taps||[])e+=gain*(far[i-delay-extra]||0);
    if(scene.clip)e=Math.max(-scene.clip,Math.min(scene.clip,e));
    return e+noise[i]+(i>=rate*startNear/1000?near[i]:0);
   });
   const ref=scene.silent?new Float32Array(size):far.slice(n*size,(n+1)*size);
   const r=gate.push(mic,{reference:ref,referenceAvailable:!scene.noReference,playback:true,mode});
   if(r.suppress)blocked++;active++;
   if(r.commit&&first===null)first=(n+1)*20;
  }
  suppression.push(blocked/active);
  if(scene.near){if(first===null)missTrials++;else if(first<startNear)earlyTrials++;else detection.push(first-startNear);}
  else if(first!==null)falseTrials++;
 }
 rows.push({mode,scene:scene.name,trials:5,falseInterruptTrials:falseTrials,earlyInterruptTrials:earlyTrials,missedUserTrials:missTrials,detectedUserTrials:detection.length,latencyMs:detection.length?{min:Math.min(...detection),max:Math.max(...detection)}:null,echoSuppressionFrameFraction:+(suppression.reduce((a,b)=>a+b,0)/5).toFixed(3)});
}
const report={kind:'synthetic-stress-not-real-speech',secondsPerTrial:seconds,seedsPerScene:5,notes:['One decision per trial; false interruption counts affected trials, not events/hour.','Far and near signals are independently filtered pseudo-random synthetic signals; no words or hardware.','Noise and near signal scales are arbitrary test amplitudes, not calibrated sound pressure.','No threshold is relaxed to make this report pass.'],rows};
const output=new URL('../eval/duplex-stress.json',import.meta.url);await mkdir(new URL('../eval/',import.meta.url),{recursive:true});await writeFile(output,JSON.stringify(report,null,2));
console.table(rows.map(r=>({mode:r.mode,scene:r.scene,false:r.falseInterruptTrials,early:r.earlyInterruptTrials,miss:r.missedUserTrials,detected:r.detectedUserTrials,latency:r.latencyMs?`${r.latencyMs.min}-${r.latencyMs.max}`:'-'})));
console.log('Saved eval/duplex-stress.json');
