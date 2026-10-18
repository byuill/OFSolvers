"""Plot per-ID tracer tracks and export a self-contained interactive replay."""
import argparse
import csv
import json
from pathlib import Path
import webbrowser

import numpy as np


def load_tracks(run):
    run = Path(run)
    if not (run/'complete').is_file():
        raise ValueError('The run has no completion marker; inspect the solver log before using its tracks.')
    with (run/'tracks.csv').open(encoding='utf-8') as stream:
        rows = [{key:float(value) for key,value in row.items()} for row in csv.DictReader(stream)]
    if not rows:
        raise ValueError('No particle tracks found.')
    times = sorted({r['time'] for r in rows})
    ids = sorted({int(r['id']) for r in rows})
    index = {id:i for i,id in enumerate(ids)}
    time_index = {t:i for i,t in enumerate(times)}
    data = np.full((len(times),len(ids),11),np.nan)
    for r in rows:
        data[time_index[r['time']],index[int(r['id'])]] = [r[k] for k in (
            'x','y','z','vx','vy','vz','active','mobilized','left_region','crossed_rim','inside_region')]
    if not np.isfinite(data).all():
        raise ValueError('Tracks contain missing or nonfinite particle states.')
    first = {int(r['id']):r for r in rows if r['time']==times[0]}
    return dict(times=np.array(times),ids=ids,data=data,
        diameter=np.array([first[i]['diameter'] for i in ids]),
        density=np.array([first[i]['density'] for i in ids]),
        pair_eligible=([bool(first[i]['pair_contact_eligible']) for i in ids]
                       if 'pair_contact_eligible' in rows[0] else None))


def bed_profile(case):
    # A fixed-bed outline is known for the bundled demonstration. Arbitrary
    # carrier geometry can be viewed with particles.pvd in ParaView.
    if (Path(case)/'scour-demo.json').is_file():
        return [[0,0],[1,0],[1.3,-.2],[1.7,-.2],[2,0],[3,0]]
    return []


def plot_carrier(run, output):
    """Actual saved velocity on the central longitudinal x-z plane, water only."""
    import tempfile
    import pyvista as pv
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.tri import Triangulation
    from foam_io import value
    run, output = Path(run).resolve(), Path(output)
    case = run.parents[2]
    config = (run/'largeTracerProperties').read_text(encoding='utf-8')
    time = float(value(config,'flowTime' if value(config,'frozenFlow')=='true' else 'endTime'))
    markers = sorted(case.glob('*.foam'))
    temporary = None
    if not markers:
        with tempfile.NamedTemporaryFile(dir=case,suffix='.foam',prefix='.tracer-view-',delete=False) as stream:
            temporary = Path(stream.name)
        markers = [temporary]
    try:
        reader = pv.OpenFOAMReader(markers[0])
        reader.disable_all_cell_arrays(); reader.enable_cell_array('U')
        # Respect a custom water-fraction field chosen for this replay.
        alpha_name = value(config,'alphaField')
        has_alpha = alpha_name!='none' and alpha_name in reader.cell_array_names
        if has_alpha: reader.enable_cell_array(alpha_name)
        reader.disable_all_patch_arrays(); reader.enable_patch_array('internalMesh')
        reader.cell_to_point_creation = False
        saved = min(reader.time_values,key=lambda t:abs(t-time))
        reader.set_active_time_value(saved)
        mesh = reader.read()['internalMesh']
    finally:
        if temporary: temporary.unlink(missing_ok=True)
    bounds = mesh.bounds
    y = (bounds[2]+bounds[3])/2
    cut = mesh.slice(normal=(0,1,0),origin=(mesh.center[0],y,mesh.center[2])).triangulate().clean()
    vertices = cut.points[:,[0,2]]
    tris = cut.faces.reshape(-1,4)[:,1:]
    uv = np.asarray(cut.cell_data['U'])
    speed = np.linalg.norm(uv,axis=1)
    alpha = np.asarray(cut.cell_data[alpha_name]) if has_alpha else np.ones(cut.n_cells)
    a = vertices[tris[:,1]]-vertices[tris[:,0]]
    b = vertices[tris[:,2]]-vertices[tris[:,0]]
    valid = np.abs(a[:,0]*b[:,1]-a[:,1]*b[:,0])>1e-14
    wet = valid & (alpha>=.5)
    if not wet.any(): raise ValueError('No water cells on the central plane at the selected time.')
    tri = Triangulation(vertices[:,0],vertices[:,1],tris)
    tri.set_mask(~wet)
    vmax = max(float(max(speed[wet])),1e-6)
    fig, ax = plt.subplots(figsize=(12,5),layout='constrained')
    ax.set_facecolor('#e8eff5')
    colors = ax.tripcolor(tri,facecolors=speed,cmap='viridis',vmin=0,vmax=vmax,rasterized=True)
    if has_alpha:
        interface = Triangulation(vertices[:,0],vertices[:,1],tris)
        interface.set_mask(~valid)
        ax.tricontour(interface,cut.cell_data_to_point_data()[alpha_name],levels=[.5],colors=['#14bfd0'],linewidths=1.5)
    centers = cut.cell_centers().points
    indices = np.flatnonzero(wet)[::max(1,int(sum(wet))//180)]
    ax.quiver(centers[indices,0],centers[indices,2],uv[indices,0],uv[indices,2],
        color='white',angles='xy',scale_units='xy',scale=12*vmax,width=.0015,pivot='mid')
    bed = bed_profile(case)
    if bed:
        bed = np.array(bed)
        ax.fill_between(bed[:,0],bed[:,1],bounds[4]-.05,color='#b8a995')
    ax.set(xlim=(bounds[0],bounds[1]),ylim=(bounds[4],bounds[5]),aspect='equal',
        xlabel='x (m)',ylabel='z (m)',title=f'Central longitudinal velocity: y = {y:g} m, t = {saved:g} s')
    fig.colorbar(colors,ax=ax,label='Water speed |U| (m/s)',shrink=.7)
    ax.text(0,-.17,'White arrows: (Ux, Uz). Cyan: water fraction 0.5. Air is masked.\n'
        'Saved transient demonstration; hydraulic convergence and turbulence have not been established.',
        transform=ax.transAxes,fontsize=9,va='top',color='#485969')
    path = output/'flow-xz.png'
    fig.savefig(path,dpi=180,bbox_inches='tight'); plt.close(fig)
    return path


def visualize(run, output=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize

    run = Path(run).resolve()
    tracks = load_tracks(run)
    output = Path(output) if output else run/'visualization'
    output.mkdir(parents=True,exist_ok=True)
    t, data, density = tracks['times'], tracks['data'], tracks['density']
    case = run.parents[2]
    bed = bed_profile(case)
    fig, (ax, az) = plt.subplots(2,1,figsize=(11,7),layout='constrained')
    norm = Normalize(float(min(density)),float(max(density))+1e-12)
    cmap = plt.get_cmap('viridis')
    for i, id in enumerate(tracks['ids']):
        valid = data[:,i,6]>0
        indices = np.flatnonzero(valid)
        last = indices[-1] if len(indices) else 0
        color = cmap(norm(density[i]))
        ax.plot(data[:last+1,i,0],data[:last+1,i,2],color=color,lw=1.2,alpha=.8)
        ax.add_patch(plt.Circle(data[last,i,[0,2]],tracks['diameter'][i]/2,
            facecolor=color,edgecolor='black',lw=.5,alpha=.8))
        ax.scatter(data[0,i,0],data[0,i,2],marker='+',s=18,color=color)
        az.plot(t[:last+1],data[:last+1,i,2],color=color,lw=1)
    if bed:
        points = np.array(bed)
        ax.fill_between(points[:,0],points[:,1],-.27,color='#b8a995',zorder=-2)
        ax.plot(points[:,0],points[:,1],color='#68503a',lw=2)
        ax.axhline(.5,color='#2584b0',ls=':',label='Initial water level')
        ax.set_xlim(0,3); ax.set_ylim(-.25,.75)
    ax.set_aspect('equal',adjustable='box')
    ax.set(xlabel='x (m)',ylabel='z (m)',title='Paths projected onto x–z; all particle y positions')
    az.set(xlabel='Time (s)',ylabel='Particle center elevation (m)',title='Rise and retention')
    for a in (ax,az): a.grid(alpha=.2)
    fig.colorbar(plt.cm.ScalarMappable(norm=norm,cmap=cmap),ax=[ax,az],label='Particle density (kg/m³)',shrink=.65)
    final = data[-1]
    counts = dict(particles=len(tracks['ids']),mobilized=int(sum(final[:,7])),
        currently_retained=int(sum((final[:,10]>0) & (final[:,6]>0))),
        ever_left_region=int(sum(final[:,8])),crossed_rim=int(sum(final[:,9])),
        escaped=int(sum(final[:,6]==0)))
    fig.suptitle(f"{run.name}: {counts['particles']} physical spheres | "
        f"mobilized {counts['mobilized']} | left region {counts['ever_left_region']} | escaped {counts['escaped']}")
    fig.savefig(output/'trajectories.png',dpi=180,bbox_inches='tight')
    fig.savefig(output/'trajectories.svg',bbox_inches='tight')
    plt.close(fig)
    # Keep SVG formatting clean in git when validation artifacts are committed.
    svg = output/'trajectories.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8')
    (output/'outcomes.json').write_text(json.dumps(counts,indent=2)+'\n',encoding='utf-8')
    payload = dict(times=t.tolist(),ids=tracks['ids'],frames=np.round(data,9).tolist(),
        diameter=tracks['diameter'].tolist(),density=density.tolist(),bed=bed,
        name=run.name,counts=counts,pairEligible=tracks['pair_eligible'])
    html = HTML.replace('__DATA__',json.dumps(payload,separators=(',',':')).replace('<','\\u003c'))
    (output/'replay.html').write_text(html,encoding='utf-8')
    return output


HTML = '''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Physical tracer replay</title>
<style>
body{font:16px system-ui,sans-serif;color:#172b3b;background:#f4f7fa;margin:24px auto;max-width:1200px;padding:0 20px}
canvas{width:100%;height:560px;background:white;border:1px solid #cbd5df;border-radius:8px}
.controls{display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin:16px 0}
input[type=range]{flex:1;min-width:200px}button,select{padding:8px;font:inherit}p{line-height:1.5}
#details{font-variant-numeric:tabular-nums}h1{font-size:25px}
</style>
<h1 id="title"></h1><p>Individual physical spheres in saved carrier flow. All particles are projected onto the selected plane.
Radii are drawn to scale. Click a sphere to inspect its ID. A dashed outline marks an escaped particle's last recorded center.</p>
<div class="controls"><button id="play">Play</button><input id="time" type="range" min="0" value="0" step="1" aria-label="Replay time">
<span id="clock"></span><label>Plane <select id="plane"><option value="xz">x–z</option><option value="xy">x–y</option><option value="yz">y–z</option></select></label>
<label>Color <select id="color"><option value="density">Density</option><option value="speed">Speed</option><option value="id">ID</option></select></label></div>
<canvas id="plot"></canvas><p id="stats"></p><p id="details">Select a particle.</p>
<p>“Mobilized” means displacement from its initial center exceeds the configured diameter threshold.
“Left region” is a first-exit event for the selected box; particles may later return. Open particles.pvd in ParaView alongside the carrier case to inspect the full 3D geometry and free surface.</p>
<script>
const D=__DATA__, canvas=document.getElementById('plot'), ctx=canvas.getContext('2d'), slider=document.getElementById('time');
const plane=document.getElementById('plane'), color=document.getElementById('color');
if(D.pairEligible){const option=document.createElement('option');option.value='pair';option.textContent='Pair contact eligibility';color.appendChild(option)}
document.getElementById('title').textContent=D.name+' — '+D.ids.length+' physical tracers';
slider.max=D.times.length-1;
let playing=false, chosen=-1, screen=[];
const bounds=[0,1,2].map(k=>{let a=Infinity,b=-Infinity;for(const f of D.frames)for(let i=0;i<f.length;i++){a=Math.min(a,f[i][k]-D.diameter[i]/2);b=Math.max(b,f[i][k]+D.diameter[i]/2)}return[a,b]});
if(D.bed.length){bounds[0]=[0,3];bounds[2]=[-.25,Math.max(.65,bounds[2][1])]}
function draw(){
 const dpr=window.devicePixelRatio||1,w=canvas.clientWidth,h=canvas.clientHeight;
 canvas.width=w*dpr;canvas.height=h*dpr;ctx.scale(dpr,dpr);ctx.clearRect(0,0,w,h);
 const frame=+slider.value, F=D.frames[frame], axes=plane.value==='xz'?[0,2]:plane.value==='xy'?[0,1]:[1,2];
 const [a,b]=axes, x0=bounds[a][0],z0=bounds[b][0], dx=Math.max(.01,bounds[a][1]-x0),dz=Math.max(.01,bounds[b][1]-z0);
 const scale=Math.min((w-110)/(dx*1.08),(h-95)/(dz*1.08)),left=(w-dx*scale)/2,bottom=(h+dz*scale)/2;
 const X=x=>left+(x-x0)*scale,Z=z=>bottom-(z-z0)*scale;
 ctx.strokeStyle='#d5dee5';ctx.lineWidth=1;ctx.fillStyle='#526675';ctx.font='12px system-ui';
 for(let j=0;j<=5;j++){let x=x0+dx*j/5,z=z0+dz*j/5;ctx.beginPath();ctx.moveTo(X(x),Z(z0));ctx.lineTo(X(x),Z(z0+dz));ctx.stroke();ctx.fillText(x.toFixed(2),X(x)-10,Z(z0)+22);ctx.beginPath();ctx.moveTo(X(x0),Z(z));ctx.lineTo(X(x0+dx),Z(z));ctx.stroke();ctx.fillText(z.toFixed(2),X(x0)-45,Z(z)+4)}
 ctx.fillText(['x','y','z'][a]+' (m)',w/2,h-12);ctx.fillText(['x','y','z'][b]+' (m)',12,25);
 if(plane.value==='xz'&&D.bed.length){ctx.strokeStyle='#806849';ctx.lineWidth=3;ctx.beginPath();D.bed.forEach((p,i)=>i?ctx.lineTo(X(p[0]),Z(p[1])):ctx.moveTo(X(p[0]),Z(p[1])));ctx.stroke();ctx.strokeStyle='#57a5ca';ctx.setLineDash([3,5]);ctx.beginPath();ctx.moveTo(X(0),Z(.5));ctx.lineTo(X(3),Z(.5));ctx.stroke();ctx.setLineDash([])}
 const values=F.map((p,i)=>color.value==='pair'?Number(D.pairEligible[i]):color.value==='density'?D.density[i]:color.value==='id'?D.ids[i]:Math.hypot(p[3],p[4],p[5]));
 let vmin=color.value==='pair'?0:Math.min(...values),vmax=color.value==='pair'?1:Math.max(...values);screen=[];
 for(let i=0;i<F.length;i++){
  const p=F[i], fraction=(values[i]-vmin)/(vmax-vmin||1), c='hsl('+(245-210*fraction)+',65%,45%)';
  ctx.strokeStyle=c;ctx.globalAlpha=.3;ctx.lineWidth=1;ctx.beginPath();
  for(let j=0;j<=frame;j++){const q=D.frames[j][i];if(j)ctx.lineTo(X(q[a]),Z(q[b]));else ctx.moveTo(X(q[a]),Z(q[b]));if(!q[6])break}ctx.stroke();ctx.globalAlpha=1;
  const x=X(p[a]),y=Z(p[b]),r=D.diameter[i]*scale/2;screen.push({x,y,r:Math.max(6,r)});
  ctx.beginPath();ctx.arc(x,y,Math.max(1,r),0,2*Math.PI);ctx.fillStyle=c;ctx.strokeStyle=i===chosen?'#000':'#385060';ctx.lineWidth=i===chosen?3:1;
  if(!p[6])ctx.setLineDash([3,3]);else ctx.fill();ctx.stroke();ctx.setLineDash([]);
 }
 document.getElementById('clock').textContent=D.times[frame].toFixed(3)+' s';
 document.getElementById('stats').textContent='Active: '+F.filter(p=>p[6]).length+' | Inside region: '+F.filter(p=>p[6]&&p[10]).length+' | Mobilized: '+F.filter(p=>p[7]).length+' | Ever left region: '+F.filter(p=>p[8]).length+' | Crossed rim: '+F.filter(p=>p[9]).length+' | '+color.options[color.selectedIndex].text+' range: '+vmin.toPrecision(4)+'–'+vmax.toPrecision(4);
 if(chosen>=0){let p=F[chosen];document.getElementById('details').textContent='ID '+D.ids[chosen]+' | diameter '+D.diameter[chosen].toPrecision(4)+' m | density '+D.density[chosen].toFixed(1)+' kg/m³ | position ('+p.slice(0,3).map(v=>v.toFixed(4)).join(', ')+') m | speed '+Math.hypot(p[3],p[4],p[5]).toFixed(4)+' m/s'+(D.pairEligible?' | Pair contact: '+(D.pairEligible[chosen]?'eligible':'excluded'):'')}
}
slider.oninput=draw;plane.onchange=draw;color.onchange=draw;window.onresize=draw;
document.getElementById('play').onclick=()=>{playing=!playing;document.getElementById('play').textContent=playing?'Pause':'Play'};
setInterval(()=>{if(playing){slider.value=(+slider.value+1)%D.times.length;draw()}},90);
canvas.onclick=e=>{const rect=canvas.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;let best=Infinity;chosen=-1;screen.forEach((p,i)=>{let d=Math.hypot(x-p.x,y-p.y);if(d<p.r+4&&d<best){best=d;chosen=i}});draw()};draw();
</script></html>'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--show',action='store_true',help='Open the interactive replay in the system browser')
    parser.add_argument('--carrier',action='store_true',help='Also plot saved central x-z carrier velocity')
    args = parser.parse_args()
    folder = visualize(args.run,args.output)
    print(folder/'trajectories.png')
    print(folder/'replay.html')
    if args.carrier:
        print(plot_carrier(args.run,folder))
    if args.show:
        webbrowser.open((folder/'replay.html').resolve().as_uri())
