"""Original editable Blender blockout, no external character assets."""
import bpy, math
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'web'/'models'/'aqi-study'
OUT.mkdir(parents=True,exist_ok=True)
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
def material(name,color):
    m=bpy.data.materials.new(name); m.diffuse_color=(*color,1); m.use_nodes=True
    p=next((n for n in m.node_tree.nodes if n.type=='BSDF_PRINCIPLED'),None)
    if p is None:
        p=m.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
        output=next((n for n in m.node_tree.nodes if n.type=='OUTPUT_MATERIAL'),None) or m.node_tree.nodes.new('ShaderNodeOutputMaterial')
        m.node_tree.links.new(p.outputs['BSDF'],output.inputs['Surface'])
    p.inputs['Base Color'].default_value=(*color,1); p.inputs['Roughness'].default_value=.8
    return m
skin=material('Skin',(0.78,.52,.39)); hair=material('Ink blue hair',(.025,.055,.085)); coat=material('Cream fabric',(.77,.69,.56)); dark=material('Navy collar',(.035,.055,.08)); accent=material('Apricot pin',(.7,.31,.16)); white=material('Eye ivory',(.94,.91,.82)); iris=material('Teal iris',(.13,.36,.36)); ink=material('Lash',(.01,.015,.022)); lip=material('Lip',(.28,.07,.09))
def ell(name,loc,scale,mat):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40,ring_count=24,location=loc)
    o=bpy.context.object; o.name=name; o.scale=scale
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    o.data.materials.append(mat)
    for f in o.data.polygons:f.use_smooth=True
    return o
# Blender +Z up, face toward -Y. All objects named for subsequent manual editing.
ell('Coat torso',(0,0,.60),(.48,.23,.52),coat)
ell('Inner shirt',(0,-.216,.79),(.215,.032,.28),dark)
for s in [-1,1]:
    o=ell('Sleeve '+str(s),(s*.47,0,.54),(.18,.22,.43),coat);o.rotation_euler[1]=s*.12
    o=ell('Collar '+str(s),(s*.19,-.20,1.02),(.11,.045,.20),dark);o.rotation_euler[1]=s*.40
ell('Neck',(0,0,1.18),(.115,.11,.20),skin)
# Shaped face rings rather than disconnected spheres for the jaw.
verts=[]; faces=[]
rings=[(1.27,.04,.075),(1.31,.14,.15),(1.40,.235,.195),(1.52,.29,.225),(1.67,.31,.24),(1.82,.30,.235),(1.96,.24,.20),(2.04,.11,.10)]
N=48
for z,rx,ry in rings:
    for i in range(N):
        a=2*math.pi*i/N; verts.append((rx*math.cos(a),ry*math.sin(a),z))
for j in range(len(rings)-1):
    for i in range(N):a=j*N+i;b=j*N+(i+1)%N;faces.append((a,b,b+N,a+N))
faces.extend([tuple(reversed(range(N))),tuple((len(rings)-1)*N+i for i in range(N))])
mesh=bpy.data.meshes.new('Face ring topology');mesh.from_pydata(verts,[],faces);mesh.update()
face=bpy.data.objects.new('Face editable base',mesh);bpy.context.collection.objects.link(face);mesh.materials.append(skin)
for p in mesh.polygons:p.use_smooth=True
mod=face.modifiers.new('Surface refinement','SUBSURF');mod.levels=2
ell('Back hair',(0,.10,1.69),(.34,.22,.43),hair)
for s in [-1,1]:
    ell('Ear '+str(s),(s*.30,0,1.59),(.045,.055,.08),skin)
    lock=ell('Side hair '+str(s),(s*.29,-.018,1.49),(.09,.17,.34),hair);lock.rotation_euler[1]=s*.08
    ell('Eye outline '+str(s),(s*.135,-.223,1.655),(.102,.021,.067),ink)
    ell('Eye white '+str(s),(s*.135,-.242,1.65),(.092,.014,.053),white)
    ell('Iris '+str(s),(s*.135,-.256,1.65),(.045,.008,.050),iris)
    ell('Pupil '+str(s),(s*.135,-.264,1.65),(.019,.005,.034),ink)
    ell('Catchlight '+str(s),(s*.135-.013,-.270,1.670),(.010,.003,.012),white)
    brow=ell('Brow '+str(s),(s*.135,-.223,1.755),(.084,.013,.009),hair);brow.rotation_euler[1]=s*.08
for i in range(7):
    x=(i-3)*.086
    o=ell('Swept fringe %02d'%i,(x,-.16,1.905+abs(x)*.12),(.072,.119,.159),hair);o.rotation_euler[1]=-.18
ell('Nose',(0,-.226,1.55),(.025,.035,.035),skin)
ell('Mouth',(0,-.194,1.443),(.053,.009,.006),lip)
ell('Pin bird body',(.322,-.153,1.84),(.052,.018,.024),accent)
ell('Pin bird head',(.359,-.153,1.868),(.018,.018,.019),accent)
# studio staging, excluded from glTF asset export
model_objects=list(bpy.context.scene.objects)
world=bpy.data.worlds.new('Studio') if not bpy.data.worlds else bpy.data.worlds[0];bpy.context.scene.world=world;world.use_nodes=True;world.node_tree.nodes['Background'].inputs[0].default_value=(.12,.16,.21,1);world.node_tree.nodes['Background'].inputs[1].default_value=.5
for name,loc,power,size in [('Key',(-3,-4,5),450,4),('Fill',(3,-2,3),250,3),('Rim',(1,2,4),500,3)]:
    bpy.ops.object.light_add(type='AREA',location=loc);o=bpy.context.object;o.name=name;o.data.energy=power;o.data.shape='DISK';o.data.size=size;o.rotation_euler=(Vector((0,0,1.4))-o.location).to_track_quat('-Z','Y').to_euler()
bpy.ops.object.camera_add(location=(.0,-4.6,1.75));cam=bpy.context.object;cam.rotation_euler=(Vector((0,0,1.28))-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.type='ORTHO';cam.data.ortho_scale=2.35;bpy.context.scene.camera=cam
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.samples=24;scene.render.resolution_x=800;scene.render.resolution_y=1000;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.render.filepath=str(OUT/'front.png')
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'aqi-study.blend'))
bpy.ops.object.select_all(action='DESELECT')
for o in model_objects:o.select_set(True)
bpy.context.view_layer.objects.active=face
bpy.ops.export_scene.gltf(filepath=str(OUT/'aqi-study.glb'),export_format='GLB',use_selection=True,export_apply=True)
bpy.ops.render.render(write_still=True)
print('AQI_STUDY_COMPLETE',OUT)
