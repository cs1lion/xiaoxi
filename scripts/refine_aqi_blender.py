"""Non-destructive second study: load v1, write separate v2 assets."""
import bpy, math, json
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'web/models/aqi-study/aqi-study.blend'
OUT=ROOT/'web/models/aqi-study-v2';OUT.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=str(SOURCE))
hair=bpy.data.materials['Ink blue hair'];skin=bpy.data.materials['Skin'];ink=bpy.data.materials['Lash']
# Replace explicitly named v1 hair placeholders only; source file is never saved.
for name in ['Back hair','Side hair -1','Side hair 1']+['Swept fringe %02d'%i for i in range(7)]:
    obj=bpy.data.objects.get(name)
    if obj:bpy.data.objects.remove(obj,do_unlink=True)
def mesh_object(name,verts,faces,material):
    mesh=bpy.data.meshes.new(name);mesh.from_pydata(verts,[],faces);mesh.update()
    o=bpy.data.objects.new(name,mesh);bpy.context.collection.objects.link(o);mesh.materials.append(material)
    for p in mesh.polygons:p.use_smooth=True
    return o
# Connected hair shell with open face, rather than a full overlapping ellipsoid.
verts=[];faces=[];rows=20;cols=64
for j in range(rows):
    t=j/(rows-1)
    for i in range(cols):
        angle=2*math.pi*i/cols
        front=max(0,-math.sin(angle))
        end=2.65-1.3*front**5
        theta=.025+t*end
        verts.append((.345*math.sin(theta)*math.cos(angle),.025+.265*math.sin(theta)*math.sin(angle),1.76+.34*math.cos(theta)))
for j in range(rows-1):
    for i in range(cols):a=j*cols+i;b=j*cols+(i+1)%cols;faces.append((a,b,b+cols,a+cols))
shell=mesh_object('Hair continuous crown',verts,faces,hair)
solid=shell.modifiers.new('Hair thickness','SOLIDIFY');solid.thickness=.012
sub=shell.modifiers.new('Hair smoothing','SUBSURF');sub.levels=1
# Curved tapered locks: editable quad ribbons, rounded cross-sections.
def strand(name,start,control,end,width):
    v=[];f=[];n=18;k=8
    for j in range(n):
        t=j/(n-1);p=(1-t)**2*Vector(start)+2*(1-t)*t*Vector(control)+t*t*Vector(end)
        w=width*(.6+.6*math.sin(math.pi*t))*(1-t)**.48+.0008
        for i in range(k):
            u=2*math.pi*i/k
            v.append((p.x+w*math.cos(u),p.y+w*.34*math.sin(u),p.z))
    for j in range(n-1):
        for i in range(k):a=j*k+i;b=j*k+(i+1)%k;f.append((a,b,b+k,a+k))
    f.extend([tuple(reversed(range(k))),tuple((n-1)*k+i for i in range(k))])
    o=mesh_object(name,v,f,hair);s=o.modifiers.new('Soft lock','SUBSURF');s.levels=2
for i in range(6):
    x=(i-2.5)*.09
    strand('Fringe tapered %02d'%i,(x*.65,-.15,2.065),(x-.04,-.29,1.97),(x-.025,-.25,1.79+abs(x)*.16),.050)
for side in [-1,1]:
    for i in range(3):
        strand('Side tapered %s %s'%(side,i),(side*(.25+i*.022),-.055+i*.055,1.98),(side*(.35+i*.012),-.12+i*.065,1.57),(side*(.28+i*.045),-.05+i*.065,1.23+i*.04),.063)
# Subtle cheek/jaw adjustment on the actual face mesh, preserving editable topology.
face=bpy.data.objects['Face editable base']
for vertex in face.data.vertices:
    z=vertex.co.z
    if z<1.52:vertex.co.x*=.94
    if 1.40<z<1.72 and vertex.co.y<0:
        vertex.co.y-=.012*max(0,1-abs(vertex.co.x)/.31)
# Reduce protruding eye layers: keep all in front of the face but closer together.
for side in [-1,1]:
    for prefix in ['Eye outline','Eye white','Iris','Pupil','Catchlight']:
        o=bpy.data.objects.get(prefix+' '+str(side))
        if o:o.scale.z*=.88
    brow=bpy.data.objects['Brow '+str(side)];brow.location.z=1.752
# Reduce nose bulge.
bpy.data.objects['Nose'].scale.y=.65
scene=bpy.context.scene;scene.cycles.samples=32
# Stats distinguish measured geometry from subjective appearance.
bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get();triangles=0
for o in scene.objects:
    if o.type=='MESH':
        evaluated=o.evaluated_get(deps);m=evaluated.to_mesh();m.calc_loop_triangles();triangles+=len(m.loop_triangles);evaluated.to_mesh_clear()
model=[o for o in scene.objects if o.type=='MESH']
bpy.ops.object.select_all(action='DESELECT')
for o in model:o.select_set(True)
bpy.context.view_layer.objects.active=face
bpy.ops.export_scene.gltf(filepath=str(OUT/'aqi-study.glb'),export_format='GLB',use_selection=True,export_apply=True)
cam=scene.camera
for name,location,target,scale in [('front',(0,-4.6,1.75),(0,0,1.28),2.35),('three-quarter',(2.3,-4,1.8),(0,0,1.35),2.2),('face',(0,-4,1.75),(0,0,1.68),1.05)]:
    cam.location=location;cam.rotation_euler=(Vector(target)-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.ortho_scale=scale
    scene.render.filepath=str(OUT/(name+'.png'));bpy.ops.render.render(write_still=True)
cam.location=(0,-4.6,1.75);cam.rotation_euler=(Vector((0,0,1.28))-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.ortho_scale=2.35
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'aqi-study.blend'))
(OUT/'validation.json').write_text(json.dumps({'source':str(SOURCE),'meshObjects':len(model),'evaluatedTriangles':triangles,'renders':['front.png','three-quarter.png','face.png'],'hasRig':False,'hasExpressions':False,'visualReview':'not performed; image input unavailable'},indent=2),encoding='utf-8')
print('AQI_V2_COMPLETE',OUT)
