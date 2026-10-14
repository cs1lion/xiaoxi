// Original geometric accessories. No imported meshes or textures.
import * as THREE from 'three';
export function createAccessories(vrm, height) {
  const head=vrm.humanoid.getRawBoneNode('head');
  const chest=vrm.humanoid.getRawBoneNode('chest')||vrm.humanoid.getRawBoneNode('spine');
  if(!head||!chest)return null;
  vrm.scene.updateMatrixWorld(true);
  const groups=[],geometries=[],materials=[];
  const gold=new THREE.MeshToonMaterial({color:0xd9aa78});
  const cream=new THREE.MeshToonMaterial({color:0xf1e4cb});materials.push(gold,cream);
  const mesh=(group,geometry,material,x,y,z,sx=1,sy=1,sz=1)=>{
    geometries.push(geometry);const m=new THREE.Mesh(geometry,material);m.position.set(x,y,z);m.scale.set(sx,sy,sz);group.add(m);return m;
  };
  function attach(bone,offset){
    const group=new THREE.Group();group.name='Aqi original accessory';
    group.position.copy(bone.getWorldPosition(new THREE.Vector3())).add(new THREE.Vector3(...offset).multiplyScalar(height));
    group.scale.setScalar(height);bone.updateWorldMatrix(true,false);
    // Start in world space then preserve world transform when parenting to raw bone.
    vrm.scene.parent.add(group);bone.attach(group);groups.push(group);return group;
  }
  const pin=attach(head,[-.072,.075,.065]);pin.userData.kind='bird-pin';
  mesh(pin,new THREE.SphereGeometry(1,20,12),gold,0,0,0,.020,.009,.004);
  mesh(pin,new THREE.SphereGeometry(1,16,10),gold,.016,.010,0,.007,.007,.004);
  const wing=mesh(pin,new THREE.SphereGeometry(1,16,10),cream,-.003,.004,.004,.012,.004,.002);wing.rotation.z=.3;
  const badge=attach(chest,[.065,.005,.10]);badge.userData.kind='collar-badge';
  const disc=mesh(badge,new THREE.CylinderGeometry(.011,.011,.003,24),gold,0,0,0);disc.rotation.x=Math.PI/2;
  mesh(badge,new THREE.SphereGeometry(1,16,10),cream,0,0,.003,.004,.004,.002);
  return {
    groups,
    setVisible(value){for(const g of groups)g.visible=!!value;},
    dispose(){for(const g of groups)g.removeFromParent();for(const g of geometries)g.dispose();for(const m of materials)m.dispose();}
  };
}
