import('./web/avatar/index.js').then(mod => {
  const avatar = new mod.Avatar();
  const cfg = {
    idle: {}, gaze: { targets: { viewer: {x:0,y:0,drift:0.1} } },
    emotions: { base: {blend:0.6, shift:{}, arousal:0.25, valence:0.15}, 得意: {blend:0.28, shift:{ParamMouthForm:0.5}, arousal:0.85, valence:0.85} },
    gaze: { targets: { viewer: {x:0,y:0,drift:0.1} } },
    gestures: {},
    triggers: {}
  };
  avatar.configure(cfg);
  console.log('Avatar configured');
  
  // 测试 backchannel 生成
  avatar.face.setMimicryMode(true); // 模拟用户说话
  let nodules = 0;
  for(let k=0;k<240*30;k++){ // 30秒
    const rms = 0.5;
    const bc = avatar.discourseGesture.getBackchannelGesture(true, rms, 1/240);
    if (bc) nodules++;
    avatar.discourseGesture.update(rms, 1/240, { userSpeaking: true, avatarSpeaking: false });
  }
  console.log('Total backchannels in 30s:', nodules);
  
  // 测试语篇手势生成
  avatar.discourseGesture.generate('你好，因为今天天气很好，所以我心情很好。', 0, 3.0);
  console.log('Gesture queue length:', avatar.discourseGesture.gestureQueue.length);
  console.log('First gesture:', avatar.discourseGesture.gestureQueue[0]);
  
  // 测试增量文本
  avatar.discourseGesture.feedTextDelta('今天');
  avatar.discourseGesture.feedTextDelta('天气');
  avatar.discourseGesture.feedTextDelta('真好。');
  console.log('After feedTextDelta, queue length:', avatar.discourseGesture.gestureQueue.length);
  
  console.log('All integration checks passed!');
}).catch(e => { console.error('FAIL', e.message); process.exit(1) });