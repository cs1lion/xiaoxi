import { buildSpeechPlan } from './speech-plan.js';

// Speech gestures use the actual audio clock, never token arrival time.
export class PerformanceDirector {
  constructor(){
    this.state='idle';this.action=null;this.queue=[];this.clock=null;
    this.lastStart=-Infinity;this.lastSpeechStart=-Infinity;this.endsAt=0;this.emotion='base';
    this.transition=null;this._transitionSequence=0;this._lastSampledAction=null;
    this._lastSampledName='rest';this._pendingTransitionReason=null;
  }
  setEmotion(name){this.emotion=name||'base';}
  onEvent(event){
    if(event.type==='recording'){this.state=event.state?'listening':'idle';if(event.state)this._clear('recording');}
    if(event.type==='thinking'){this.state='thinking';this._clear('thinking');}
    if(['interrupted','playback_stopped'].includes(event.type))this.stop(event.type);
  }
  _clear(reason='stop'){
    this.action=null;this.queue.length=0;this.endsAt=0;this.lastSpeechStart=-Infinity;
    if(reason!=='stop'||!this._pendingTransitionReason)this._pendingTransitionReason=reason;
  }
  stop(reason='stop'){this._clear(reason);this.state='listening';}

  speak(text,clock,start,duration,options={}){
    if(typeof text!=='string'||!Number.isFinite(clock?.currentTime)||
       !Number.isFinite(start)||start<0||!Number.isFinite(duration)||duration<=0)return;
    if(this.clock&&this.clock!==clock){this._clear('clock_changed');this.lastStart=-Infinity;}
    // A backwards audio schedule supersedes an old stream; forward fragments
    // append to it without replacing an in-progress anchored gesture.
    if(start<this.lastSpeechStart-.001)this._clear('superseded');
    this.clock=clock;this.lastSpeechStart=start;this.state='speaking';
    this.endsAt=Math.max(this.endsAt,start+duration);
    this._prune(clock.currentTime);
    const plan=buildSpeechPlan(text,duration,options);
    if(plan){
      const actions=plan.cues.map(cue=>{
        const anchorStart=start+cue.anchorStart,anchorEnd=start+cue.anchorEnd;
        const from=Math.max(start,anchorStart-.25);
        const end=Math.min(start+duration,Math.max(anchorStart+.65,Math.min(anchorEnd+.3,anchorStart+1.65)));
        return {...cue,name:cue.gesture,start:from,duration:end-from,
          anchorStart,anchorEnd,strength:cue.strength,source:'llm',alignment:plan.alignment};
      });
      // Leave recovery space before the next cue so two intentions cannot pull
      // the same arm in different directions at once.
      for(let i=0;i<actions.length;i++){
        const a=actions[i],next=actions[i+1];
        if(next)a.duration=Math.min(a.duration,next.start-a.start-.08);
        if(a.duration<.3||a.start+a.duration<=clock.currentTime)continue;
        const booked=[this.action,...this.queue].filter(Boolean);
        if(a.start-this.lastStart<.9||booked.some(b=>
          a.start<b.start+b.duration+.08&&a.start+a.duration+.08>b.start))continue;
        this.queue.push(a);
      }
      this.queue.sort((a,b)=>a.start-b.start);
      return;
    }
    // Older servers and explicitly marked fallback plans retain the four
    // established keyword gestures and their conservative four-second cooldown.
    if(start-this.lastStart<4)return;
    const name=/你好|嗨|再见/.test(text)?'wave':/不是|不对|别|不能/.test(text)?'deny':/我觉得|我想|我来/.test(text)?'self':/[？?]|为什么|怎么/.test(text)||this.emotion==='调侃'?'palm':null;
    if(name){
      const action={name,start:start+.15,duration:Math.min(2.6,Math.max(.4,duration-.15)),
        strength:1,intent:null,anchor:null,alignment:'estimated',source:'fallback'};
      if(action.start+action.duration<=clock.currentTime)return;
      const booked=[this.action,...this.queue].filter(Boolean);
      if(booked.some(a=>action.start<a.start+a.duration+.08&&action.start+action.duration+.08>a.start))return;
      if(!this.action&&!this.queue.length)this.action=action;
      else {this.queue.push(action);this.queue.sort((a,b)=>a.start-b.start);}
      this.lastStart=start;
    }
  }

  _prune(now){
    if(this.action&&now>=this.action.start+this.action.duration){
      this.action=null;
      this._pendingTransitionReason??='complete';
    }
    this.queue=this.queue.filter(a=>now<a.start+a.duration);
  }
  preview(name,clock){
    if(!['wave','deny','self','palm'].includes(name))return;
    this.queue.length=0;this.clock=clock;
    this.action={name,start:clock.currentTime,duration:2.4,strength:1,intent:null,anchor:null,alignment:null,source:'preview'};
  }
  _withTransition(frame,action){
    if(action!==this._lastSampledAction){
      const to=frame.name||'rest';
      this.transition=Object.freeze({id:++this._transitionSequence,
        reason:to==='rest'?(this._pendingTransitionReason||'complete'):
          this._lastSampledName==='rest'?'start':'replace',
        from:this._lastSampledName,to});
      this._lastSampledAction=action;this._lastSampledName=to;
    }
    this._pendingTransitionReason=null;
    return {...frame,transition:this.transition};
  }
  sample(){
    const now=this.clock?.currentTime??0;
    if(this.state==='speaking'&&now>=this.endsAt)this.state='idle';
    this._prune(now);
    if(!this.action&&this.queue.length&&this.queue[0].start<=now){
      this.action=this.queue.shift();
      if(this.action.source==='llm')this.lastStart=Math.max(this.lastStart,this.action.start);
    }
    const empty={state:this.state,name:null,weight:0,phase:0,strength:1,intent:null,anchor:null,alignment:null,source:null};
    const a=this.action;if(!a)return this._withTransition(empty,null);
    const t=now-a.start;if(t<0)return this._withTransition(empty,null);
    const ramp=Math.min(.35,a.duration*.3);const w=Math.min(1,t/ramp,(a.duration-t)/ramp);
    return this._withTransition({state:this.state,name:a.name,weight:w*w*(3-2*w),phase:t/a.duration,
      strength:a.strength??1,intent:a.intent??null,anchor:a.anchor??null,
      alignment:a.alignment??null,source:a.source??null},a);
  }
}
