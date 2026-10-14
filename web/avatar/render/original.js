/* Original procedural character study. No third-party mesh/texture assets.
 * Real 3D toon meshes; not a VRM and not a production sculpt. */
import * as T from 'three';
const clamp=(v,a=-1,b=1)=>Math.max(a,Math.min(b,Number.isFinite(v)?v:0));
export class OriginalRig {
  mount(container) {
    this.container=container; this.materials=[]; this.geometries=[];
    this.renderer=new T.WebGLRenderer({alpha:true,antialias:true});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio,2));
    this.renderer.setClearColor(0,0); this.renderer.outputColorSpace=T.SRGBColorSpace;
    this.renderer.domElement.style.cssText='width:100%;height:100%;display:block';
    container.append(this.renderer.domElement);
    this.scene=new T.Scene();
    this.scene.add(new T.HemisphereLight(0xfff4e6,0x667c92,2));
    const light=new T.DirectionalLight(0xffffff,2.4);light.position.set(-3,5,7);this.scene.add(light);
    this.camera=new T.PerspectiveCamera(30,1,.01,100);this.camera.position.set(0,1.25,5.4);this.camera.lookAt(0,1.15,0);
    const mat=(color)=>{const m=new T.MeshToonMaterial({color});this.materials.push(m);return m;};
    const skin=mat('#efc9b2'),hair=mat('#273746'),highlight=mat('#425968'),cream=mat('#e9dfcf'),dark=mat('#263344'),accent=mat('#d79d7c'),white=mat('#fff9ee'),iris=mat('#669c9b'),ink=mat('#192632'),mouth=mat('#743e48');
    const sphere=(parent,m,x,y,z,sx,sy,sz)=>{const g=new T.SphereGeometry(1,40,28);this.geometries.push(g);const mesh=new T.Mesh(g,m);mesh.position.set(x,y,z);mesh.scale.set(sx,sy,sz);parent.add(mesh);return mesh;};
    const line=(parent,m,points,r=.018)=>{const curve=new T.CatmullRomCurve3(points.map(p=>new T.Vector3(...p)));const g=new T.TubeGeometry(curve,24,r,8,false);this.geometries.push(g);const mesh=new T.Mesh(g,m);parent.add(mesh);return mesh;};
    this.body=new T.Group();this.scene.add(this.body);
    sphere(this.body,cream,0,.38,0,.64,.67,.30);
    sphere(this.body,dark,0,.61,.258,.28,.38,.065);
    sphere(this.body,cream,-.58,.35,0,.23,.53,.25);sphere(this.body,cream,.58,.35,0,.23,.53,.25);
    const collarL=sphere(this.body,dark,-.25,.91,.19,.16,.25,.07);collarL.rotation.z=.5;
    const collarR=sphere(this.body,dark,.25,.91,.19,.16,.25,.07);collarR.rotation.z=-.5;
    line(this.body,accent,[[0,.30,.323],[0,.52,.327],[0,.77,.32]],.014);
    sphere(this.body,skin,0,1.08,0,.14,.27,.14);
    this.head=new T.Group();this.head.position.y=1.60;this.body.add(this.head);
    // Back hair and side silhouette are actual meshes, not a flat portrait.
    sphere(this.head,hair,0,.02,-.095,.53,.61,.39);
    sphere(this.head,skin,0,-.015,.16,.425,.49,.32);
    sphere(this.head,skin,-.416,-.06,.105,.07,.12,.065);sphere(this.head,skin,.416,-.06,.105,.07,.12,.065);
    for(const side of [-1,1]) {
      const lock=sphere(this.head,hair,side*.435,-.21,.11,.135,.45,.19);lock.rotation.z=side*.08;
      line(this.head,highlight,[[side*.47,.17,.22],[side*.48,-.10,.28],[side*.46,-.48,.23]],.009);
    }
    // Swept fringe: large clean strands, eyes and brows remain visible.
    for(let i=0;i<7;i++) {
      const x=(i-3)*.115;
      const strand=sphere(this.head,hair,x,.35+Math.abs(x)*.10,.31,.105,.225,.15);
      strand.rotation.z=-.28;
    }
    this.eyes=[];this.brows=[];
    for(const side of [-1,1]) {
      const group=new T.Group();group.position.set(side*.18,.025,.455);this.head.add(group);
      sphere(group,ink,0,0,-.006,.146,.105,.029);
      sphere(group,white,0,-.005,.015,.129,.085,.023);
      const gaze=new T.Group();group.add(gaze);
      sphere(gaze,iris,0,-.008,.04,.066,.078,.017);
      sphere(gaze,ink,0,-.005,.055,.026,.055,.009);
      sphere(gaze,white,-.022,.025,.065,.018,.022,.005);
      this.eyes.push({group,gaze});
      const brow=new T.Group();brow.position.set(side*.18,.185,.444);this.head.add(brow);
      line(brow,hair,[[-.105,0,0],[0,.024,0],[.105,.009,0]],.014);this.brows.push(brow);
      sphere(this.head,accent,side*.265,-.16,.415,.07,.023,.009);
    }
    sphere(this.head,skin,0,-.12,.479,.032,.046,.04);
    this.mouth=sphere(this.head,mouth,0,-.28,.436,.083,.011,.009);
    // Original abstract bird pin on character's left (viewer's right).
    const pin=new T.Group();pin.position.set(.455,.23,.34);pin.rotation.z=-.22;this.head.add(pin);
    sphere(pin,accent,0,0,0,.075,.04,.018);sphere(pin,accent,.05,.032,0,.025,.026,.018);
    const wing=sphere(pin,cream,-.02,.015,.017,.045,.014,.008);wing.rotation.z=-.35;
    this.observer=new ResizeObserver(()=>this.resize());this.observer.observe(container);this.resize();this.draw({},0);
  }
  resize(){const r=this.container.getBoundingClientRect();this.renderer.setSize(Math.max(r.width,1),Math.max(r.height,1),false);this.camera.aspect=Math.max(r.width,1)/Math.max(r.height,1);this.camera.position.z=this.camera.aspect<.75?6.4:5.4;this.camera.updateProjectionMatrix();}
  setSecondaryMotion(scale){this.secondary=scale;}
  draw(p,dt){
    this.head.rotation.set(clamp(p.ParamAngleY)*.22,clamp(p.ParamAngleX)*.30,-clamp(p.ParamAngleZ)*.20);
    this.body.rotation.z=-clamp(p.ParamBodyAngleZ)*.07;
    this.body.position.y=clamp(p.ParamBreath)*.009;
    this.eyes.forEach(({group,gaze},i)=>{const v=p[i?'ParamEyeROpen':'ParamEyeLOpen'];group.scale.y=Math.max(.045,clamp(v??1,0,1));gaze.position.x=clamp(p.ParamEyeBallX)*.028;gaze.position.y=-clamp(p.ParamEyeBallY)*.018;});
    this.brows.forEach((b,i)=>{b.position.y=.185+clamp(p[i?'ParamBrowRY':'ParamBrowLY'])*.065;b.rotation.z=clamp(p[i?'ParamBrowRAngle':'ParamBrowLAngle'])*.3;});
    this.mouth.scale.y=.011+clamp(p.ParamMouthOpenY,0,1)*.095;
    this.mouth.scale.x=.083+clamp(p.ParamMouthForm)*.025-clamp(p.ParamMouthRound)*.016;
    this.renderer.render(this.scene,this.camera);
  }
  dispose(){this.observer?.disconnect();for(const g of this.geometries||[])g.dispose();for(const m of this.materials||[])m.dispose();this.renderer?.dispose();this.renderer?.forceContextLoss();this.renderer?.domElement.remove();}
}
