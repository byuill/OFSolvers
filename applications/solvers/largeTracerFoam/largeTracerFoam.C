/* One-way Newtonian tracking of 1..999 individual physical spheres in saved
 * OpenFOAM fields. No changes to the carrier solution. OpenFOAM.com v1912. */
#include "TracerPhysics.H"
#include "CarrierFields.H"
#include "triSurfaceSearch.H"
#include "wallPolyPatch.H"
#include <fstream>
#include <iomanip>
#include <algorithm>

using namespace Foam;

struct TracerWall
{
    word name;
    triSurface surface;
    triSurfaceSearch search;
    TracerWall(const polyPatch& patch, const fvMesh& mesh)
    : name(patch.name()), surface(makeSurface(patch,mesh)), search(surface) {}
    static triSurface makeSurface(const polyPatch& patch, const fvMesh& mesh)
    {
        DynamicList<face> triangles;
        forAll(patch,i) patch[i].triangles(mesh.points(),triangles);
        List<labelledTri> tris(triangles.size());
        forAll(tris,i) tris[i]=labelledTri(triangles[i][0],triangles[i][1],triangles[i][2],0);
        triSurface original(tris,mesh.points());
        return triSurface(original.localFaces(),original.localPoints());
    }
    scalar contact(Tracer& p, scalar e, scalar friction) const
    {
        scalar r=p.d/2;
        auto hit=search.tree().findNearest(p.x,sqr(r*(1+1e-6)));
        if (!hit.hit()) return 0;
        vector delta=p.x-hit.hitPoint();
        vector inward=-surface.faceNormals()[hit.index()];
        scalar distance=mag(delta);
        bool fluidSide=(delta&inward)>=0;
        vector n=distance>SMALL && fluidSide ? delta/distance : inward;
        scalar gap=fluidSide ? distance : -distance;
        if (gap<r)
        {
            p.x+=(r-gap+1e-10*r)*n;
            tracerImpulse(p,nullptr,n,e,friction);
            return (r-gap)/r;
        }
        return 0;
    }
};

static bool inBox(const point& p, const point& lo, const point& hi)
{
    for (direction k=0;k<3;++k) if (p[k]<lo[k] || p[k]>hi[k]) return false;
    return true;
}

class TracerOutput
{
    fileName path_;
    std::ofstream history_, events_, pvd_;
    label frame_=0;
public:
    TracerOutput(const Time& time, const IOdictionary& cfg)
    {
        word name=cfg.get<word>("runName");
        requireTracer(name.size()>0 && name!="." && name!=".." && name.find('/')==std::string::npos,
            "runName must be a simple nonempty directory name");
        path_=time.path()/"postProcessing"/"largeTracers"/name;
        requireTracer(!exists(path_),"Tracer output already exists: "+path_+". Choose a new runName; existing tracks are never overwritten.");
        mkDir(path_);
        OFstream config(path_/"largeTracerProperties");
        cfg.writeHeader(config); cfg.dictionary::write(config,false);
        history_.open((path_/"tracks.csv").c_str());
        events_.open((path_/"events.csv").c_str());
        pvd_.open((path_/"particles.pvd").c_str());
        requireTracer(history_.good() && events_.good() && pvd_.good(),"Cannot create tracer output files");
        history_<<std::setprecision(16);
        events_<<std::setprecision(16)<<"time,id,event\n";
        history_<<"time,id,x,y,z,vx,vy,vz,omega_x,omega_y,omega_z,diameter,density,water_fraction,active,mobilized,left_region,crossed_rim,max_displacement,inside_region\n";
        pvd_<<"<?xml version=\"1.0\"?>\n<VTKFile type=\"Collection\" version=\"0.1\" byte_order=\"LittleEndian\"><Collection>\n";
        Info<<"Tracer output: "<<path_<<nl;
    }
    void event(scalar time,label id,const char* name) {events_<<time<<','<<id<<','<<name<<'\n';}
    void write(scalar time, const std::vector<Tracer>& particles)
    {
        label active=0;
        for (const auto& p:particles)
        {
            active+=p.active;
            history_<<time<<','<<p.id<<','<<p.x.x()<<','<<p.x.y()<<','<<p.x.z()<<','
                <<p.v.x()<<','<<p.v.y()<<','<<p.v.z()<<','<<p.omega.x()<<','<<p.omega.y()<<','<<p.omega.z()<<','
                <<p.d<<','<<p.rho<<','<<p.water<<','<<p.active<<','<<(p.mobilized>=0)<<','
                <<(p.leftRegion>=0)<<','<<(p.crossedRim>=0)<<','<<p.maxDisplacement<<','<<p.insideRegion<<'\n';
        }
        fileName name="particles_"+Foam::name(frame_++)+".vtk";
        std::ofstream vtk((path_/name).c_str());
        requireTracer(vtk.good(),"Cannot write particle VTK snapshot");
        vtk<<std::setprecision(16)<<"# vtk DataFile Version 3.0\nIndividual physical tracers\nASCII\nDATASET POLYDATA\nPOINTS "<<active<<" double\n";
        for (const auto& p:particles) if (p.active) vtk<<p.x.x()<<' '<<p.x.y()<<' '<<p.x.z()<<'\n';
        vtk<<"VERTICES "<<active<<' '<<2*active<<'\n';
        for (label i=0;i<active;++i) vtk<<"1 "<<i<<'\n';
        vtk<<"POINT_DATA "<<active<<"\nVECTORS velocity double\n";
        for (const auto& p:particles) if (p.active) vtk<<p.v.x()<<' '<<p.v.y()<<' '<<p.v.z()<<'\n';
        for (const word& field:{word("id"),word("diameter"),word("density"),word("water_fraction")})
        {
            vtk<<"SCALARS "<<field<<" double 1\nLOOKUP_TABLE default\n";
            for (const auto& p:particles) if (p.active)
                vtk<<(field=="id" ? p.id : field=="diameter" ? p.d : field=="density" ? p.rho : p.water)<<'\n';
        }
        pvd_<<std::setprecision(16)<<"<DataSet timestep=\""<<time<<"\" group=\"\" part=\"0\" file=\""<<name<<"\"/>\n";
        history_.flush(); events_.flush(); pvd_.flush();
        requireTracer(history_.good() && events_.good() && pvd_.good() && vtk.good(),"Failed to write particle output");
    }
    void complete(const std::vector<Tracer>& particles)
    {
        std::ofstream summary((path_/"summary.csv").c_str());
        summary<<std::setprecision(16)<<"id,diameter,density,mass,initial_x,initial_y,initial_z,mobilized_time,left_region_time,crossed_rim_time,escaped_time,max_displacement,active,initially_inside_region,inside_region\n";
        for (const auto& p:particles)
            summary<<p.id<<','<<p.d<<','<<p.rho<<','<<p.mass<<','<<p.initial.x()<<','<<p.initial.y()<<','<<p.initial.z()<<','
                <<p.mobilized<<','<<p.leftRegion<<','<<p.crossedRim<<','<<p.escaped<<','<<p.maxDisplacement<<','<<p.active<<','<<p.initiallyInside<<','<<p.insideRegion<<'\n';
        pvd_<<"</Collection></VTKFile>\n"; pvd_.flush(); summary.close();
        requireTracer(summary.good() && pvd_.good(),"Cannot finish tracer output");
        std::ofstream done((path_/"complete").c_str()); done<<"Tracking completed successfully\n";
    }
};

int main(int argc,char *argv[])
{
    argList::noParallel();
    argList::addNote("One-way, finite-radius physical sphere tracking in saved static carrier fields. 1..999 particles, serial replay.");
    #include "setRootCase.H"
    #include "createTime.H"
    IOdictionary cfg(IOobject("largeTracerProperties",runTime.constant(),runTime,
        IOobject::MUST_READ,IOobject::NO_WRITE));
    scalar start=cfg.get<scalar>("startTime"), end=cfg.get<scalar>("endTime");
    requireTracer(std::isfinite(start) && std::isfinite(end) && start>=0 && end>start,
        "Tracking times must be finite, 0<=startTime<endTime");
    scalar outputDt=positiveTracer(cfg,"outputInterval",0.02);
    scalar maxDt=positiveTracer(cfg,"maxDeltaT",0.002);
    scalar minDt=positiveTracer(cfg,"minDeltaT",1e-9);
    scalar displacementCo=positiveTracer(cfg,"maxDisplacementFraction",0.1);
    requireTracer(displacementCo<=0.25 && minDt<=maxDt,"Use maxDisplacementFraction<=0.25 and minDeltaT<=maxDeltaT");
    runTime.setTime(start,0);
    #include "createMesh.H"
    forAll(mesh.boundaryMesh(),i)
    {
        word type=mesh.boundaryMesh()[i].type();
        requireTracer(type!="empty" && type!="wedge" && !mesh.boundary()[i].coupled(),
            "Use a three-dimensional reconstructed mesh with wall/open boundaries (no empty, wedge, cyclic, or processor patches)");
    }
    TracerCarrier carrier(mesh,cfg,start,end);
    std::vector<std::unique_ptr<TracerWall>> walls;
    forAll(mesh.boundaryMesh(),i) if (isA<wallPolyPatch>(mesh.boundaryMesh()[i]))
        walls.emplace_back(new TracerWall(mesh.boundaryMesh()[i],mesh));

    label count=cfg.get<label>("count");
    requireTracer(count>0 && count<1000,"count must be in 1..999");
    label seed=cfg.lookupOrDefault<label>("seed",42);
    requireTracer(seed>=0,"seed must be nonnegative");
    std::mt19937_64 rng(seed);
    TracerDistribution diameters(cfg.subDict("diameter")), densities(cfg.subDict("density"));
    vector g=cfg.lookupOrDefault<vector>("g",vector(0,0,-9.81));
    for (direction k=0;k<3;++k) requireTracer(std::isfinite(g[k]),"g must be finite");
    scalar rhoW=positiveTracer(cfg,"rhoWater",1000), muW=positiveTracer(cfg,"muWater",0.001);
    scalar rhoA=positiveTracer(cfg,"rhoAir",1.2), muA=positiveTracer(cfg,"muAir",1.8e-5);
    scalar added=cfg.lookupOrDefault<scalar>("addedMassCoefficient",0.5);
    scalar e=cfg.lookupOrDefault<scalar>("restitution",0.1), friction=cfg.lookupOrDefault<scalar>("friction",0.4);
    requireTracer(std::isfinite(added) && added>=0 && std::isfinite(e) && e>=0 && e<=1
        && std::isfinite(friction) && friction>=0,"Invalid added mass, restitution, or friction");
    bool collisions=cfg.lookupOrDefault<bool>("particleCollisions",true);
    label contactIterations=cfg.lookupOrDefault<label>("contactIterations",32);
    requireTracer(contactIterations>0 && contactIterations<=1024,"contactIterations must be 1..1024");
    const dictionary& region=cfg.subDict("seedRegion");
    point lo=region.get<point>("min"), hi=region.get<point>("max");
    point roiLo=cfg.lookupOrDefault<point>("retentionMin",lo), roiHi=cfg.lookupOrDefault<point>("retentionMax",hi);
    for (direction k=0;k<3;++k)
        requireTracer(std::isfinite(lo[k]) && std::isfinite(hi[k]) && hi[k]>lo[k]
            && std::isfinite(roiLo[k]) && std::isfinite(roiHi[k]) && roiHi[k]>roiLo[k],"Invalid seed or retention box");
    scalar minWet=region.lookupOrDefault<scalar>("minWaterFraction",0.99);
    requireTracer(std::isfinite(minWet) && minWet>0 && minWet<=1,"Initial minWaterFraction must be in (0,1]");
    word placement=region.lookupOrDefault<word>("placement","volume");
    requireTracer(placement=="volume" || placement=="bed","placement must be volume or bed");
    wordList bedPatches=region.lookupOrDefault<wordList>("bedPatches",wordList(1,"bed"));
    if (placement=="bed")
    {
        requireTracer(mag(g.x())+mag(g.y())<SMALL && g.z()<0,"Bed placement currently requires downward z gravity");
        for (const auto& name:bedPatches)
        {
            bool found=false; for (const auto& wall:walls) found|=wall->name==name;
            requireTracer(found,"Unknown wall patch for bed seeding: "+name);
        }
    }
    word initialVelocity=cfg.lookupOrDefault<word>("initialVelocity","zero");
    requireTracer(initialVelocity=="zero" || initialVelocity=="flow","initialVelocity must be zero or flow");
    scalar mobilization=positiveTracer(cfg,"mobilizationDiameters",1);
    scalar rim=cfg.lookupOrDefault<scalar>("rimElevation",GREAT);
    requireTracer(std::isfinite(rim),"rimElevation must be finite");
    std::uniform_real_distribution<scalar> uniform(0,1);
    std::vector<Tracer> particles;
    pointField explicitPositions;
    vectorField explicitVelocities;
    if (cfg.found("initialPositions")) cfg.lookup("initialPositions") >> explicitPositions;
    if (cfg.found("initialVelocities")) cfg.lookup("initialVelocities") >> explicitVelocities;
    requireTracer(explicitPositions.empty() || explicitPositions.size()==count,"initialPositions length must equal count");
    requireTracer(explicitVelocities.empty() || explicitVelocities.size()==count,"initialVelocities length must equal count");
    for (const auto& x:explicitPositions) requireTracer(std::isfinite(magSqr(x)),"initialPositions must be finite");
    for (const auto& v:explicitVelocities) requireTracer(std::isfinite(magSqr(v)),"initialVelocities must be finite");
    for (label i=0;i<count;++i)
    {
        // Draw size and density ONCE. Placement rejection must not bias either distribution.
        scalar d=diameters.sample(rng), rho=densities.sample(rng), r=d/2;
        point p(Zero); CarrierSample flow; bool placed=false;
        for (label attempt=0;attempt<(explicitPositions.empty()?20000:1);++attempt)
        {
            for (direction k=0;k<3;++k) p[k]=lo[k]+uniform(rng)*(hi[k]-lo[k]);
            if (!explicitPositions.empty()) p=explicitPositions[i];
            else if (placement=="bed")
            {
                point top(p.x(),p.y(),hi.z()), bottom(p.x(),p.y(),lo.z());
                bool hitBed=false; scalar highest=-GREAT;
                for (const auto& wall:walls) if (bedPatches.found(wall->name))
                {
                    auto hit=wall->search.tree().findLine(top,bottom);
                    if (hit.hit() && hit.hitPoint().z()>highest)
                    {p=hit.hitPoint()-r*(1+1e-6)*wall->surface.faceNormals()[hit.index()]; highest=hit.hitPoint().z(); hitBed=true;}
                }
                if (!hitBed) continue;
            }
            if (!inBox(p,lo,hi) || !carrier.clearance(p,r*(1-1e-8))) continue;
            bool overlaps=false;
            for (const auto& other:particles) if (mag(p-other.x)<(d+other.d)/2) {overlaps=true; break;}
            if (overlaps) continue;
            flow=carrier.sample(p,r,start);
            if (flow.water+1e-12<minWet) continue;
            placed=true; break;
        }
        requireTracer(placed,"Could not place particle "+Foam::name(i)+" (d="+Foam::name(d)+
            ") fully inside the initial wetted region without overlap. Enlarge the seed region, reduce count/sizes, or check alpha.");
        particles.emplace_back(i,p,d,rho);
        particles.back().water=flow.water;
        particles.back().initiallyInside=particles.back().insideRegion=inBox(p,roiLo,roiHi);
        if (initialVelocity=="flow") particles.back().v=flow.water*flow.uw+(1-flow.water)*flow.ua;
        if (!explicitVelocities.empty()) particles.back().v=explicitVelocities[i];
        // Ensure an extremely large object did not overflow its physical mass.
        requireTracer(std::isfinite(particles.back().mass) && particles.back().mass>VSMALL,"Particle mass is out of range");
    }
    scalar totalVolume=0; for (const auto& p:particles) totalVolume+=p.volume;
    Info<<"Seeded "<<count<<" physical spheres, total volume "<<totalVolume<<" m^3. One-way coupling; check local volume loading."<<nl;
    TracerOutput output(runTime,cfg);
    output.write(start,particles);
    scalar t=start, nextOutput=min(end,start+outputDt);
    label steps=0, maxSteps=cfg.lookupOrDefault<label>("maxSteps",10000000);
    requireTracer(maxSteps>0,"maxSteps must be positive");
    while (t<end-1e-12)
    {
        requireTracer(++steps<=maxSteps,"Exceeded maxSteps; tracking stopped without a completion marker");
        carrier.update(t);
        scalar dt=min(maxDt,min(nextOutput-t,carrier.nextTime()-t));
        std::vector<vector> target(particles.size(),Zero), forcing(particles.size(),Zero);
        std::vector<scalar> beta(particles.size(),0), inertia(particles.size(),0);
        for (auto& p:particles) if (p.active)
        {
            auto s=carrier.sample(p.x,p.d/2,t); p.water=s.water;
            scalar f=s.water, displaced=p.volume*(f*rhoW+(1-f)*rhoA);
            scalar bw=f*tracerDrag(p.d,rhoW,muW,mag(s.uw-p.v));
            scalar ba=(1-f)*tracerDrag(p.d,rhoA,muA,mag(s.ua-p.v));
            beta[p.id]=bw+ba;
            target[p.id]=(bw*s.uw+ba*s.ua)/beta[p.id];
            inertia[p.id]=p.mass+added*displaced;
            forcing[p.id]=(p.mass-displaced)*g+(1+added)*p.volume*(f*rhoW*s.aw+(1-f)*rhoA*s.aa);
            vector acceleration=(beta[p.id]*(target[p.id]-p.v)+forcing[p.id])/inertia[p.id];
            scalar length=displacementCo*p.d/2;
            dt=min(dt,length/max(mag(p.v),scalar(1e-9)));
            dt=min(dt,std::sqrt(2*length/max(mag(acceleration),scalar(1e-9))));
            dt=min(dt,0.2*inertia[p.id]/beta[p.id]);
        }
        requireTracer(dt>=minDt || (nextOutput-t<minDt && dt>0),"Required timestep below minDeltaT; reduce stiffness/sizes or lower minDeltaT");
        requireTracer(std::isfinite(dt) && dt>0,"Invalid tracer timestep");
        for (auto& p:particles) if (p.active)
        {
            // Exact velocity update for frozen linearized drag over this substep.
            scalar rate=beta[p.id]/inertia[p.id], q=-std::expm1(-rate*dt);
            vector equilibrium=target[p.id]+forcing[p.id]/beta[p.id];
            vector old=p.v;
            p.v+=q*(equilibrium-p.v);
            scalar mu=p.water*muW+(1-p.water)*muA;
            p.omega*=std::exp(-8*constant::mathematical::pi*mu*pow3(p.d/2)*dt
                /(0.4*p.mass*sqr(p.d/2)));
            // Exact integral of that velocity (not forward Euler position).
            p.x+=equilibrium*dt+(old-equilibrium)*(q/rate);
        }
        for (label iteration=0;iteration<contactIterations;++iteration)
        {
            scalar penetration=0;
            for (auto& p:particles) if (p.active) for (const auto& wall:walls)
                penetration=max(penetration,wall->contact(p,e,friction));
            std::vector<label> order;
            if (collisions)
            {
                for (label i=0;i<count;++i) if (particles[i].active) order.push_back(i);
                std::sort(order.begin(),order.end(),[&](label i,label j)
                    {return particles[i].x.x()-particles[i].d/2 < particles[j].x.x()-particles[j].d/2;});
            }
            for (label ii=0;ii<label(order.size());++ii)
                for (label jj=ii+1;jj<label(order.size());++jj)
                {
                    label i=order[ii], j=order[jj];
                    auto& a=particles[i]; auto& b=particles[j];
                    if (b.x.x()-b.d/2 > a.x.x()+a.d/2) break;
                    vector delta=a.x-b.x; scalar distance=mag(delta), radius=(a.d+b.d)/2;
                    if (distance<radius)
                    {
                        penetration=max(penetration,(radius-distance)/radius);
                        vector n=distance>SMALL ? delta/distance : vector(1,0,0);
                        scalar overlap=radius-distance+1e-10*radius;
                        a.x+=overlap*b.mass/(a.mass+b.mass)*n;
                        b.x-=overlap*a.mass/(a.mass+b.mass)*n;
                        tracerImpulse(a,&b,n,e,friction);
                    }
                }
            if (penetration<1e-10) break;
            requireTracer(iteration+1<contactIterations || penetration<1e-5,
                "Contact iterations did not converge. Reduce timestep/loading or increase contactIterations; no completion marker was written.");
        }
        t+=dt;
        for (auto& p:particles) if (p.active)
        {
            requireTracer(std::isfinite(magSqr(p.x)) && std::isfinite(magSqr(p.v)),"Nonfinite particle state");
            p.maxDisplacement=max(p.maxDisplacement,mag(p.x-p.initial));
            if (p.mobilized<0 && p.maxDisplacement>=mobilization*p.d)
            {p.mobilized=t; output.event(t,p.id,"mobilized");}
            bool inside=inBox(p.x,roiLo,roiHi);
            if (p.leftRegion<0 && p.insideRegion && !inside)
            {p.leftRegion=t; output.event(t,p.id,"left_region");}
            p.insideRegion=inside;
            if (p.crossedRim<0 && p.initial.z()<rim && p.x.z()>=rim)
            {p.crossedRim=t; output.event(t,p.id,"crossed_rim");}
            if (carrier.cell(p.x)<0)
            {p.active=false; p.escaped=t; output.event(t,p.id,"escaped");}
        }
        if (t>=nextOutput-1e-12)
        {
            carrier.update(t);
            for (auto& p:particles) if (p.active) p.water=carrier.sample(p.x,p.d/2,t).water;
            output.write(t,particles);
            if (t>=end-1e-12) break;
            nextOutput=min(end,nextOutput+outputDt);
        }
    }
    output.complete(particles);
    Info<<"Completed "<<steps<<" tracer substeps; carrier fields were not modified."<<nl;
    return 0;
}
