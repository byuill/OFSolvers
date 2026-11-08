"""Plot physical DEM bedload histories and a saved central x-z fluid transect."""
import argparse
import csv
from pathlib import Path
import numpy as np


def numeric_csv(path):
    with Path(path).open(encoding='utf-8') as stream:names=next(csv.reader(stream))
    raw=np.loadtxt(path,delimiter=',',skiprows=1,ndmin=2,encoding='utf-8')
    if raw.shape[1]!=len(names) or not np.isfinite(raw).all():
        raise ValueError('Missing or nonfinite CFD–DEM output data.')
    return {name:raw[:,i] for i,name in enumerate(names)}


def plot_dem(run,output=None,flow=False):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Circle
    run=Path(run).resolve();output=Path(output) if output else run/'visualization'
    if not (run/'complete').is_file(): raise ValueError('Complete the run and inspect its log before plotting.')
    output.mkdir(parents=True,exist_ok=True)
    tracks=numeric_csv(run/'tracks.csv');budget=numeric_csv(run/'budget.csv')
    if len(tracks['time'])==0: raise ValueError('No particle states to plot.')
    final=tracks['time']==max(tracks['time']);mobile=tracks['mobile'][final]>0
    speed=np.sqrt(sum(tracks[name][final]**2 for name in ('vx','vy','vz')))
    fig=plt.figure(figsize=(12,8),layout='constrained');grid=fig.add_gridspec(2,2,height_ratios=(2,1))
    ax=fig.add_subplot(grid[0,:]);flux=fig.add_subplot(grid[1,0]);momentum=fig.add_subplot(grid[1,1])
    x,z,d=tracks['x'][final],tracks['z'][final],tracks['diameter'][final]
    circles=[Circle((a,b),c/2) for a,b,c in zip(x[mobile],z[mobile],d[mobile])]
    colored=PatchCollection(circles,cmap='viridis',edgecolor='#243848',linewidth=.25)
    colored.set_array(speed[mobile]);ax.add_collection(colored)
    fixed=[Circle((a,b),c/2) for a,b,c in zip(x[~mobile],z[~mobile],d[~mobile])]
    ax.add_collection(PatchCollection(fixed,facecolor='#777777',edgecolor='#333333',linewidth=.25))
    ax.autoscale();ax.set_aspect('equal');ax.set(xlabel='Downstream x (m)',ylabel='Elevation z (m)',
        title=f'{run.name}: {len(x)} physical grains at {max(tracks["time"]):g} s\nAll y positions projected; fixed basal grains in gray')
    fig.colorbar(colored,ax=ax,label='Mobile grain speed (m/s)',shrink=.6)
    flux.plot(budget['time'],budget['bedload_flux_x'],label='Below bedload elevation')
    flux.plot(budget['time'],budget['total_solid_flux_x'],'--',label='All mobile grains')
    flux.set(xlabel='Time (s)',ylabel='Solid volume flux per width (m²/s)');flux.legend(fontsize=8);flux.grid(alpha=.2)
    momentum.plot(budget['time'],budget['fluid_px'],label='Fluid')
    momentum.plot(budget['time'],budget['particle_px'],label='Mobile grains')
    momentum.set(xlabel='Time (s)',ylabel='Downstream momentum (kg m/s)');momentum.legend();momentum.grid(alpha=.2)
    fig.savefig(output/'bedload.png',dpi=180);plt.close(fig)
    if flow:plot_flow(run,output)
    return output


def plot_flow(run,output):
    import pyvista as pv
    import matplotlib.pyplot as plt
    from matplotlib.tri import Triangulation
    case=run.parents[2];reader=pv.OpenFOAMReader(case/'case.foam')
    time=float(numeric_csv(run/'budget.csv')['time'][-1])
    saved=min(reader.time_values,key=lambda t:abs(t-time))
    if abs(saved-time)>1e-9: raise ValueError('Reconstruct the final saved CFD time before requesting the flow transect.')
    reader.disable_all_cell_arrays();reader.enable_cell_array('U');reader.enable_cell_array('porosity')
    reader.disable_all_patch_arrays();reader.enable_patch_array('internalMesh');reader.cell_to_point_creation=False
    reader.set_active_time_value(saved);mesh=reader.read()['internalMesh'];y=mesh.center[1]
    cut=mesh.slice(normal=(0,1,0),origin=mesh.center).triangulate().clean()
    vertices=cut.points[:,[0,2]];triangles=cut.faces.reshape(-1,4)[:,1:]
    a=vertices[triangles[:,1]]-vertices[triangles[:,0]];b=vertices[triangles[:,2]]-vertices[triangles[:,0]]
    tri=Triangulation(vertices[:,0],vertices[:,1],triangles)
    tri.set_mask(np.abs(a[:,0]*b[:,1]-a[:,1]*b[:,0])<1e-16)
    u=np.asarray(cut['U']);speed=np.linalg.norm(u,axis=1)
    fig,ax=plt.subplots(figsize=(12,5),layout='constrained')
    colors=ax.tripcolor(tri,facecolors=speed,cmap='viridis',vmin=0,rasterized=True)
    centers=cut.cell_centers().points
    select=np.arange(0,cut.n_cells,max(1,int(np.ceil(cut.n_cells/100))))
    arrow_length=.04*np.ptp(vertices[:,0])
    ax.quiver(centers[select,0],centers[select,2],u[select,0],u[select,2],color='white',
        angles='xy',scale_units='xy',scale=max(speed.max(),1e-8)/arrow_length,width=.0015)
    ax.set(aspect='equal',xlabel='x (m)',ylabel='z (m)',title=f'Coupled intrinsic fluid speed: y={y:g} m, t={saved:g} s')
    fig.colorbar(colors,ax=ax,label='|U| (m/s)',shrink=.7)
    fig.savefig(output/'flow-xz.png',dpi=180);plt.close(fig)
    return output/'flow-xz.png'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run',type=Path)
    parser.add_argument('--output',type=Path);parser.add_argument('--flow',action='store_true')
    args=parser.parse_args();print(plot_dem(args.run,args.output,args.flow))
