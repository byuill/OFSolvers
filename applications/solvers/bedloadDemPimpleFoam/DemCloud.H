/* SPDX-License-Identifier: GPL-3.0-or-later
 * Conservative partitioned CFD--DEM coupling through the public LAMMPS C API.
 * The serial GRANULAR engine lives on the CFD master; fluid mapping is MPI.
 */
#ifndef DemCloud_H
#define DemCloud_H
#include "DemPhysics.H"
#include "meshSearch.H"
#include "zeroGradientFvPatchFields.H"
#include "IFstream.H"
#include "library.h"
#include <vector>
#include <utility>
#include <fstream>
#include <cstdint>
#include <algorithm>

namespace Foam
{
class DemCloud
{
    const fvMesh& mesh_;
    const IOdictionary& cfg_;
    meshSearch search_;
    boundBox box_;
    FixedList<bool,3> periodic_;
    void* engine_=nullptr;
    scalar rho_,nu_,young_,poisson_,e_,mu_,rollingK_,rollingD_,rollingMu_;
    vector gravity_;
    scalar kernelRatio_,minPorosity_,demDt_,responseFraction_,displacementFraction_;
    label samples_,maxSubcycles_;
    word drag_;
    string contact_;
    fileName output_;
    std::ofstream tracks_,budget_,pvd_;
    label frame_=0;
    int64_t startStep_=0,endStep_=0;
    scalar subDt_=0;
    bool recording_=false;
    std::vector<std::vector<std::pair<label,scalar>>> weights_;
    vectorField fluid_,gradP_,impulse_;
    scalarField beta_;
    DynamicList<vector> offsets_;
    vector lastImpulse_=Zero;

    static scalar radical(label n,label base)
    {scalar v=0,f=1;while(n){f/=base;v+=f*(n%base);n/=base;}return v;}
    void checkEngine() const
    {
        if(lammps_has_error(engine_))
        {
            char message[4096];lammps_get_last_error_message(engine_,message,sizeof(message));
            requireDem(false,string("LAMMPS: ")+message);
        }
    }
    void command(const string& text)
    {lammps_command(engine_,text.c_str());checkEngine();}
    string quotePath(const fileName& path) const
    {
        requireDem(path.find_first_of("\"$\n\r") == std::string::npos,
            "LAMMPS case paths cannot contain quotes, dollar signs or newlines");
        return "\""+path+"\"";
    }
    void readParticles()
    {
        if(Pstream::master())
        {
            label n=label(lammps_get_natoms(engine_));
            requireDem(lammps_extract_setting(engine_,"nlocal")==n,"LAMMPS must have one serial rank");
            int* ids=static_cast<int*>(lammps_extract_atom(engine_,"id"));
            int* types=static_cast<int*>(lammps_extract_atom(engine_,"type"));
            int* images=static_cast<int*>(lammps_extract_atom(engine_,"image"));
            double** x=static_cast<double**>(lammps_extract_atom(engine_,"x"));
            double** v=static_cast<double**>(lammps_extract_atom(engine_,"v"));
            double** w=static_cast<double**>(lammps_extract_atom(engine_,"omega"));
            double* r=static_cast<double*>(lammps_extract_atom(engine_,"radius"));
            double* m=static_cast<double*>(lammps_extract_atom(engine_,"rmass"));
            position.setSize(n);unwrapped.setSize(n);velocity.setSize(n);omega.setSize(n);radius.setSize(n);mass.setSize(n);type.setSize(n);
            boolList seen(n,false);
            for(label j=0;j<n;++j)
            {
                label i=ids[j]-1;
                requireDem(i>=0&&i<n&&!seen[i],"Particle IDs must be unique and consecutive from 1");seen[i]=true;
                position[i]=point(x[j][0],x[j][1],x[j][2]);velocity[i]=vector(v[j][0],v[j][1],v[j][2]);
                int image[3];lammps_decode_image_flags(images[j],image);unwrapped[i]=position[i];
                for(direction k=0;k<3;++k)if(periodic_[k])unwrapped[i][k]+=image[k]*box_.span()[k];
                omega[i]=vector(w[j][0],w[j][1],w[j][2]);radius[i]=r[j];mass[i]=m[j];type[i]=types[j];
                requireDem(std::isfinite(magSqr(position[i]))&&std::isfinite(magSqr(velocity[i]))
                    &&std::isfinite(magSqr(omega[i]))&&std::isfinite(radius[i])&&radius[i]>0
                    &&std::isfinite(mass[i])&&mass[i]>0,"Nonfinite or nonpositive DEM particle state");
                requireDem(type[i]==1||type[i]==2,"Use type 1 for mobile grains, type 2 for fixed roughness");
                requireDem(type[i]!=2||mag(velocity[i])+mag(omega[i])<1e-14,"Fixed grains must have zero velocity/spin");
            }
        }
        Pstream::scatter(position);Pstream::scatter(velocity);Pstream::scatter(omega);
        Pstream::scatter(unwrapped);
        Pstream::scatter(radius);Pstream::scatter(mass);Pstream::scatter(type);
    }
    static void callback(void* context,int64_t step,int n,int* ids,double**,double** f)
    {static_cast<DemCloud*>(context)->forces(step,n,ids,f);}
    void forces(int64_t step,int n,int* ids,double** f)
    {
        double** v=static_cast<double**>(lammps_extract_atom(engine_,"v"));
        for(int j=0;j<n;++j)
        {
            label i=ids[j]-1;
            vector slip=fluid_[i]-vector(v[j][0],v[j][1],v[j][2]);
            vector drag=beta_[i]*slip;
            scalar volume=4*constant::mathematical::pi*pow3(radius[i])/3;
            vector force=drag-rho_*volume*gradP_[i]+(mass[i]-rho_*volume)*gravity_;
            for(direction k=0;k<3;++k) f[j][k]=force[k];
            // Velocity-Verlet uses half an impulse at the two interval ends.
            // run pre=yes recomputes the first force with the current CFD data.
            if(recording_)
            {
                scalar factor=(step==startStep_||step==endStep_)?.5:1;
                impulse_[i]+=subDt_*factor*drag;
            }
        }
    }
    point wrap(point p) const
    {
        for(direction k=0;k<3;++k) if(periodic_[k])
        {
            scalar length=box_.span()[k];
            p[k]-=std::floor((p[k]-box_.min()[k])/length)*length;
        }
        return p;
    }
    void mapping()
    {
        weights_.clear();weights_.resize(position.size());
        scalarField total(position.size(),0);
        forAll(position,i)
        {
            auto& stencil=weights_[i];
            for(const vector& q:offsets_)
            {
                label c=search_.findCell(wrap(position[i]+kernelRatio_*radius[i]*q));
                if(c<0) continue;
                auto found=std::find_if(stencil.begin(),stencil.end(),[&](const std::pair<label,scalar>& w){return w.first==c;});
                if(found==stencil.end()) stencil.emplace_back(c,1); else found->second+=1;
                total[i]+=1;
            }
        }
        Pstream::listCombineGather(total,plusEqOp<scalar>());Pstream::listCombineScatter(total);
        forAll(position,i)
        {
            requireDem(total[i]>0,"Particle kernel has no support in the CFD mesh");
            for(auto& w:weights_[i]) w.second/=total[i];
        }
    }
    void geometry()
    {
        FixedList<bool,3> solid;
        for(direction k=0;k<3;++k){solid[k]=false;periodic_[k]=false;}
        forAll(mesh_.boundaryMesh(),p)
        {
            const polyPatch& patch=mesh_.boundaryMesh()[p];
            if(patch.type()=="processor") continue;
            const bool cyclic=patch.type()=="cyclic"||patch.type()=="processorCyclic";
            const vectorField normals(mesh_.boundary()[p].nf());
            requireDem(patch.type()=="wall"||cyclic||patch.type()=="symmetryPlane",
                "DEM coupling currently requires an axis-aligned periodic/walled box; unsupported patch "+patch.name());
            if(!cyclic)
            {
                const auto& velocity=mesh_.lookupObject<volVectorField>("U").boundaryField()[p];
                requireDem(velocity.type()=="noSlip"||velocity.type()=="slip"||velocity.type()=="fixedValue"||velocity.type()=="symmetryPlane",
                    "DEM box walls need impermeable noSlip/slip/fixedValue/symmetryPlane velocity conditions");
                forAll(velocity,f)requireDem(mag(velocity[f]&normals[f])<1e-12,"DEM box wall velocity must have zero normal flux");
            }
            for(const vector& n:normals)
            {
                direction axis=0;for(direction k=1;k<3;++k) if(mag(n[k])>mag(n[axis]))axis=k;
                requireDem(mag(n[axis])>1-1e-8,"DEM plane walls require an axis-aligned box");
                if(cyclic)periodic_[axis]=true;else solid[axis]=true;
            }
            for(const point& centre:mesh_.boundary()[p].Cf())
            {
                bool onBox=false;
                for(direction k=0;k<3;++k)onBox|=min(mag(centre[k]-box_.min()[k]),mag(centre[k]-box_.max()[k]))<1e-8*mag(box_.span());
                requireDem(onBox,"Internal CFD obstacles are unsupported; represent roughness with fixed DEM grains");
            }
        }
        for(direction k=0;k<3;++k)
        {
            reduce(periodic_[k],orOp<bool>());reduce(solid[k],orOp<bool>());
            requireDem(periodic_[k]!=solid[k],"Each DEM box axis must be either periodic or walled, matching the CFD mesh");
        }
    }
public:
    pointField position,initial,unwrapped;
    vectorField velocity,omega;
    scalarField radius,mass;
    labelList type;
    DemCloud(const fvMesh& mesh,const IOdictionary& cfg,scalar rho,scalar nu,const vector& g)
    :mesh_(mesh),cfg_(cfg),search_(mesh),box_(mesh.points(),true),rho_(rho),nu_(nu),gravity_(g)
    {
        young_=demPositive(cfg,"youngModulus",1e8);poisson_=cfg.lookupOrDefault<scalar>("poissonRatio",.25);
        e_=cfg.lookupOrDefault<scalar>("restitution",.8);mu_=cfg.lookupOrDefault<scalar>("friction",.5);
        rollingK_=cfg.lookupOrDefault<scalar>("rollingStiffness",0);
        rollingD_=cfg.lookupOrDefault<scalar>("rollingDamping",0);rollingMu_=cfg.lookupOrDefault<scalar>("rollingFriction",0);
        requireDem(std::isfinite(poisson_)&&poisson_>=0&&poisson_<.5&&std::isfinite(e_)&&e_>0&&e_<=1
            &&std::isfinite(mu_)&&mu_>=0&&std::isfinite(rollingK_)&&rollingK_>=0
            &&std::isfinite(rollingD_)&&rollingD_>=0&&std::isfinite(rollingMu_)&&rollingMu_>=0,"Invalid DEM contact properties");
        requireDem((rollingK_==0&&rollingD_==0&&rollingMu_==0)||(rollingK_>0&&rollingMu_>0),
            "Rolling resistance requires positive rollingStiffness and rollingFriction");
        kernelRatio_=demPositive(cfg,"kernelRadiusRatio",3);
        requireDem(kernelRatio_>=1,"kernelRadiusRatio must be >=1 (physical particle radius)");
        minPorosity_=cfg.lookupOrDefault<scalar>("minimumPorosity",.1);
        requireDem(std::isfinite(minPorosity_)&&minPorosity_>0&&minPorosity_<1,"minimumPorosity must lie in (0,1)");
        samples_=cfg.lookupOrDefault<label>("volumeSamples",128);
        requireDem(samples_>=32&&samples_<=8192,"volumeSamples must be 32..8192");
        maxSubcycles_=cfg.lookupOrDefault<label>("maxDEMSubcycles",1000000);
        requireDem(maxSubcycles_>0,"maxDEMSubcycles must be positive");
        demDt_=demPositive(cfg,"maxDEMDeltaT",2e-6);
        responseFraction_=demPositive(cfg,"responseTimeFraction",.1);
        displacementFraction_=demPositive(cfg,"couplingDisplacementFraction",.1);
        requireDem(responseFraction_<=.2&&displacementFraction_<=.25,"Coupling response/displacement fractions must be <=0.2/0.25");
        drag_=cfg.lookupOrDefault<word>("dragModel","DiFelice");
        requireDem(drag_=="DiFelice"||drag_=="SchillerNaumann","dragModel must be DiFelice or SchillerNaumann");
        for(label i=0;i<samples_;++i)
        {
            scalar r=std::cbrt((i+.5)/samples_),z=1-2*radical(i+1,2),a=2*constant::mathematical::pi*radical(i+1,3);
            offsets_.append(r*vector(sqrt(max(1-sqr(z),scalar(0)))*cos(a),sqrt(max(1-sqr(z),scalar(0)))*sin(a),z));
        }
        geometry();
        contact_="hertz/material "+demNumber(young_)+" "+demNumber(e_)+" "+demNumber(poisson_)
            +" tangential mindlin_rescale NULL 1.0 "+demNumber(mu_)+" damping coeff_restitution";
        if(rollingK_>0)contact_+=" rolling sds "+demNumber(rollingK_)+" "+demNumber(rollingD_)+" "+demNumber(rollingMu_);
        if(Pstream::master())
        {
            requireDem(!lammps_config_has_mpi_support(),"Build the embedded LAMMPS library with BUILD_MPI=OFF; the CFD master owns DEM");
            requireDem(lammps_config_has_package("GRANULAR")&&lammps_config_has_exceptions(),"LAMMPS needs GRANULAR and exception support");
            char arg0[]="bedloadDemPimpleFoam",a1[]="-log",a3[]="-screen",a4[]="none";
            string logName=mesh.time().globalPath()/("log.lammps-"+mesh.time().timeName());
            char* args[]={arg0,a1,const_cast<char*>(logName.c_str()),a3,a4};engine_=lammps_open_no_mpi(5,args,nullptr);
            requireDem(engine_,"Cannot open LAMMPS library");
            requireDem(lammps_version(engine_)>=20250722&&lammps_extract_setting(engine_,"tagint")==4,
                "Use LAMMPS 22Jul2025 or newer with default 32-bit atom IDs (SMALLBIG)");
            command("units si");command("atom_style sphere");command("atom_modify map array");
            string boundary="boundary";for(direction k=0;k<3;++k)boundary+=periodic_[k]?" p":" f";
            command(boundary);command("newton off");
            if(mesh.time().value()>SMALL)
            {
                fileName state=mesh.time().globalPath()/mesh.time().timeName()/"uniform/DEM";
                IFstream input(state/"couplingState");
                requireDem(input.good(),"Missing DEM couplingState checkpoint");
                dictionary metadata(input);
                requireDem(metadata.get<label>("schema")==1&&mag(metadata.get<scalar>("time")-mesh.time().value())<1e-10
                    &&metadata.get<string>("contact")==contact_,"DEM checkpoint time or contact parameters disagree with the restart");
                command("read_restart "+quotePath(state/"lammps.restart"));
            }
            else command("read_data "+quotePath(mesh.time().globalPath()/"constant/DEM/particles.data"));
            double lo[3],hi[3],xy,yz,xz;int pb[3],change;
            lammps_extract_box(engine_,lo,hi,&xy,&yz,&xz,pb,&change);
            requireDem(mag(xy)+mag(yz)+mag(xz)<1e-14,"LAMMPS box must be orthogonal");
            for(direction k=0;k<3;++k)requireDem(mag(lo[k]-box_.min()[k])+mag(hi[k]-box_.max()[k])<1e-9*mag(box_.span())
                &&bool(pb[k])==periodic_[k],"CFD and DEM boxes/periodicity disagree");
            command("comm_modify vel yes");
            // The SAME pair_style invokes settings() without replacing the
            // restored pair object (LAMMPS Input::pair_style). This also resets
            // the radius-derived global cutoff after a 2025 granular restart.
            // Do not reissue pair_coeff: retain its tangential/rolling history.
            command("pair_style granular");
            if(mesh.time().value()<=SMALL)
                command("pair_coeff * * "+contact_);
            command("group fixed type 2");command("group mobile subtract all fixed");
            // Native freeze marks basal grains as infinite-inertia contact
            // partners. Merely excluding them from nve/sphere is insufficient.
            command("fix anchor fixed freeze");
            command("fix integrate mobile nve/sphere");
            command("fix hydro all external pf/callback 1 1");
        }
        readParticles();initial=unwrapped;
        fluid_.setSize(position.size(),Zero);gradP_.setSize(position.size(),Zero);
        impulse_.setSize(position.size(),Zero);beta_.setSize(position.size(),0);
        if(Pstream::master())
        {
            scalar minR=GREAT;for(scalar r:radius)minR=min(minR,r);
            if(radius.size())
            {
                command("neighbor "+demNumber(.4*minR)+" bin");command("neigh_modify delay 0 every 1 check yes");
                for(direction k=0;k<3;++k)if(!periodic_[k])
                    command("fix wall"+Foam::name(k)+" all wall/gran granular "+contact_+" "+word(k==0?"xplane":k==1?"yplane":"zplane")
                        +" "+demNumber(box_.min()[k])+" "+demNumber(box_.max()[k]));
            }
            else command("neighbor 0.001 nsq");
            lammps_set_fix_external_callback(engine_,"hydro",callback,this);checkEngine();
            command("thermo 0");command("timestep "+demNumber(demDt_));command("run 0 post no");
            Info<<"Embedded LAMMPS version "<<lammps_version(engine_)<<"; "<<position.size()<<" physical grains"<<nl;
        }
        // Rayleigh limit for the smallest/least-dense sphere. Contact stiffness
        // convergence still needs an overlap and DEM timestep study.
        scalar shear=young_/(2*(1+poisson_)),rayleigh=GREAT;
        forAll(radius,i)
        {
            scalar density=mass[i]/(4*constant::mathematical::pi*pow3(radius[i])/3);
            rayleigh=min(rayleigh,constant::mathematical::pi*radius[i]*sqrt(density/shear)/(.1631*poisson_+.8766));
        }
        demDt_=min(demDt_,.1*rayleigh);
        mapping();
        if(Pstream::master())openOutput();
    }
    ~DemCloud(){if(engine_)lammps_close(engine_);}
    void porosity(volScalarField& eps)
    {
        scalarField solid(mesh_.nCells(),0);
        forAll(position,i)for(const auto& w:weights_[i])
            solid[w.first]+=w.second*4*constant::mathematical::pi*pow3(radius[i])/3;
        eps.primitiveFieldRef()=scalar(1)-solid/mesh_.V();eps.correctBoundaryConditions();
        requireDem(gMin(eps.primitiveField())>=minPorosity_,
            "Mapped porosity below minimumPorosity; refine the mapping/kernel/mesh. No volume clipping is allowed");
        scalar volume=0;for(scalar r:radius)volume+=4*constant::mathematical::pi*pow3(r)/3;
        scalar error=gSum((scalar(1)-eps.primitiveField())*mesh_.V())-volume;
        requireDem(mag(error)<1e-10*max(volume,scalar(VSMALL))+1e-20,"Particle-volume mapping is not conservative");
    }
    scalar prepare(const volVectorField& U,const volScalarField& p,const volScalarField& eps)
    {
        volVectorField grad(fvc::grad(p));
        fluid_=Zero;gradP_=Zero;scalarField wet(position.size(),0);
        forAll(position,i)for(const auto& w:weights_[i])
        {fluid_[i]+=w.second*U[w.first];gradP_[i]+=w.second*grad[w.first];wet[i]+=w.second*eps[w.first];}
        Pstream::listCombineGather(fluid_,plusEqOp<vector>());Pstream::listCombineScatter(fluid_);
        Pstream::listCombineGather(gradP_,plusEqOp<vector>());Pstream::listCombineScatter(gradP_);
        Pstream::listCombineGather(wet,plusEqOp<scalar>());Pstream::listCombineScatter(wet);
        scalar limit=GREAT;scalarField fluidRate(mesh_.nCells(),0);
        forAll(position,i)
        {
            beta_[i]=demDrag(2*radius[i],rho_,nu_,mag(fluid_[i]-velocity[i]),wet[i],drag_);
            requireDem(std::isfinite(beta_[i])&&beta_[i]>0,"Invalid DEM drag coefficient");
            if(type[i]==1)limit=min(limit,responseFraction_*mass[i]/beta_[i]);
            limit=min(limit,displacementFraction_*2*radius[i]/max(mag(velocity[i]),scalar(1e-12)));
            for(const auto& w:weights_[i])fluidRate[w.first]+=w.second*beta_[i]/(rho_*mesh_.V()[w.first]*eps[w.first]);
        }
        limit=min(limit,responseFraction_/max(gMax(fluidRate),scalar(1e-30)));
        return limit;
    }
    void advance(scalar dt,volVectorField& reaction,volScalarField& eps)
    {
        pointField previous(unwrapped);
        scalar requested=std::ceil(dt/demDt_);
        requireDem(std::isfinite(requested)&&requested<=maxSubcycles_,"Too many DEM subcycles; reduce the CFD step or revise verified stiffness settings");
        label n=max(label(1),label(requested));
        subDt_=dt/n;impulse_=Zero;
        if(Pstream::master())
        {
            startStep_=*static_cast<int64_t*>(lammps_extract_global(engine_,"ntimestep"));endStep_=startStep_+n;
            command("timestep "+demNumber(subDt_));recording_=true;
            command("run "+Foam::name(n)+" pre yes post no");recording_=false;
        }
        Pstream::scatter(impulse_);readParticles();
        requireDem(position.size()==previous.size(),"DEM lost particles; this solver has a closed/periodic grain inventory");
        reaction=dimensionedVector("zero",dimAcceleration,Zero);lastImpulse_=Zero;
        forAll(position,i)
        {
            lastImpulse_+=impulse_[i];
            for(const auto& w:weights_[i])reaction[w.first]-=w.second*impulse_[i]/(rho_*dt*mesh_.V()[w.first]);
            vector dx=unwrapped[i]-previous[i];
            requireDem(mag(dx)<=.5*2*radius[i],"Particle moved > half a diameter per coupling step; reduce the CFD step");
        }
        reaction.correctBoundaryConditions();
        vector fluidImpulse=rho_*dt*fvc::domainIntegrate(reaction).value();
        requireDem(mag(fluidImpulse+lastImpulse_)<1e-10*max(mag(lastImpulse_),scalar(1e-30))+1e-18,
            "CFD--DEM drag impulse exchange is not conservative");
        mapping();porosity(eps);
        Info<<"DEM coupling: subcycles="<<n<<" minPorosity="<<gMin(eps.primitiveField())
            <<" impulseResidual="<<mag(fluidImpulse+lastImpulse_)<<" kg m/s"<<nl;
    }
    void openOutput();
    void write(scalar continuity);
    void finish()
    {
        if(Pstream::master())
        {
            pvd_<<"</Collection></VTKFile>\n";pvd_.flush();
            requireDem(pvd_.good(),"Cannot finish DEM particle collection");
            OFstream done(output_/"complete");done<<"CFD--DEM run completed\n";
        }
    }
};
}
#endif
