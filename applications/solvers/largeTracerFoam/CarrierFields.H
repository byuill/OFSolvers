#ifndef CarrierFields_H
#define CarrierFields_H
#include "interpolationCellPoint.H"
#include "meshSearch.H"
#include "treeDataFace.H"
#include <memory>
#include <vector>

namespace Foam
{
struct CarrierSample
{
    scalar water=0;
    vector uw=Zero, ua=Zero, aw=Zero, aa=Zero;
};

class TracerFrame
{
public:
    volVectorField U;
    volTensorField grad;
    std::unique_ptr<volScalarField> alpha;
    interpolationCellPoint<vector> ui;
    interpolationCellPoint<tensor> gi;
    std::unique_ptr<interpolationCellPoint<scalar>> ai;
    TracerFrame(const fvMesh& mesh, const word& time, const word& alphaName)
    : U(IOobject("U",time,mesh,IOobject::MUST_READ,IOobject::NO_WRITE,false),mesh),
      grad(fvc::grad(U)), ui(U), gi(grad)
    {
        requireTracer(U.dimensions()==dimVelocity,"Carrier U has incorrect dimensions");
        if (alphaName!="none")
        {
            alpha.reset(new volScalarField(IOobject(alphaName,time,mesh,
                IOobject::MUST_READ,IOobject::NO_WRITE,false),mesh));
            requireTracer(alpha->dimensions()==dimless,"Carrier alpha must be dimensionless");
            ai.reset(new interpolationCellPoint<scalar>(*alpha));
        }
    }
};

class TracerCarrier
{
    const fvMesh& mesh_;
    meshSearch search_;
    word alphaName_;
    std::vector<instant> times_;
    std::unique_ptr<TracerFrame> a_, b_;
    label bracket_=-1;
    bool frozen_;
    std::vector<vector> offsets_;
    static scalar radical(label n, label base)
    {
        scalar value=0, f=1;
        while (n) {f/=base; value+=f*(n%base); n/=base;}
        return value;
    }
public:
    TracerCarrier(const fvMesh& mesh, const dictionary& cfg, scalar start, scalar end)
    : mesh_(mesh), search_(mesh), alphaName_(cfg.get<word>("alphaField")),
      frozen_(cfg.lookupOrDefault<bool>("frozenFlow",false))
    {
        label samples=cfg.lookupOrDefault<label>("volumeSamples",128);
        requireTracer(samples>=32 && samples<=8192,"volumeSamples must be 32..8192");
        for (label i=0;i<samples;++i)
        {
            scalar r=std::cbrt((i+0.5)/samples), z=1-2*radical(i+1,2);
            scalar phi=2*constant::mathematical::pi*radical(i+1,3);
            offsets_.push_back(r*vector(sqrt(max(1-sqr(z),scalar(0)))*cos(phi),
                sqrt(max(1-sqr(z),scalar(0)))*sin(phi),z));
        }
        const instantList all=mesh.time().times();
        forAll(all,i)
        {
            if (all[i].name()=="constant") continue;
            fileName path=mesh.time().path()/all[i].name();
            requireTracer(!isDir(path/"polyMesh"),"Moving/changing meshes are not supported; use a static reconstructed case");
            IOobject u("U",all[i].name(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE,false);
            if (u.typeHeaderOk<volVectorField>(true)) times_.push_back(all[i]);
        }
        requireTracer(!times_.empty(),"No saved carrier U fields found");
        if (frozen_)
        {
            scalar snapshot=cfg.lookupOrDefault<scalar>("flowTime",start);
            bool found=false;
            for (const auto& t:times_) if (mag(t.value()-snapshot)<1e-9)
            {times_={t}; found=true; break;}
            requireTracer(found,"Frozen flowTime must match a saved U time");
        }
        else requireTracer(times_.size()>=2 && start>=times_.front().value()-1e-10
            && end<=times_.back().value()+1e-10,
            "Transient tracking needs >=2 saved U times covering the entire tracking interval. Use frozenFlow explicitly for one snapshot.");
        update(start);
    }
    label cell(const point& x) const {return search_.findCell(x);}
    bool clearance(const point& x, scalar radius) const
    {
        return cell(x)>=0 && !search_.boundaryTree().findNearest(x,sqr(radius)).hit();
    }
    void update(scalar time)
    {
        label k=0;
        if (!frozen_) while (k+2<label(times_.size()) && time>=times_[k+1].value()-1e-12) ++k;
        if (k==bracket_) return;
        b_.reset(); a_.reset();
        a_.reset(new TracerFrame(mesh_,times_[k].name(),alphaName_));
        if (!frozen_) b_.reset(new TracerFrame(mesh_,times_[k+1].name(),alphaName_));
        bracket_=k;
        Info<< "Carrier bracket: " << times_[k].name();
        if (b_) Info<< " .. " << times_[k+1].name();
        Info<< nl;
    }
    scalar nextTime() const {return frozen_ ? GREAT : times_[bracket_+1].value();}
    CarrierSample sample(const point& x, scalar radius, scalar time) const
    {
        CarrierSample s;
        scalar dt=b_ ? times_[bracket_+1].value()-times_[bracket_].value() : 1;
        scalar f=b_ ? min(max((time-times_[bracket_].value())/dt,scalar(0)),scalar(1)) : 0;
        label centre=cell(x);
        requireTracer(centre>=0,"Attempt to sample carrier outside the domain");
        // Volume-average the undisturbed flow and phase fractions over each sphere.
        for (const auto& offset:offsets_)
        {
            point p=x+radius*offset;
            label c=cell(p);
            if (c<0) {c=centre; p=x;} // truncated sphere at an open outlet
            vector ua=a_->ui.interpolate(p,c), u=ua, dudt=Zero;
            tensor grad=a_->gi.interpolate(p,c);
            scalar alpha=a_->ai ? a_->ai->interpolate(p,c) : 1;
            requireTracer(std::isfinite(alpha),"Nonfinite carrier water fraction");
            alpha=min(max(alpha,scalar(0)),scalar(1));
            if (b_)
            {
                vector ub=b_->ui.interpolate(p,c);
                u=(1-f)*ua+f*ub; dudt=(ub-ua)/dt;
                grad=(1-f)*grad+f*b_->gi.interpolate(p,c);
                if (b_->ai)
                {
                    scalar other=b_->ai->interpolate(p,c);
                    requireTracer(std::isfinite(other),"Nonfinite carrier water fraction");
                    alpha=(1-f)*alpha+f*min(max(other,scalar(0)),scalar(1));
                }
            }
            vector acc=dudt+(u&grad);
            requireTracer(std::isfinite(magSqr(u)) && std::isfinite(magSqr(acc)),"Nonfinite carrier velocity/acceleration");
            s.water+=alpha; s.uw+=alpha*u; s.ua+=(1-alpha)*u;
            s.aw+=alpha*acc; s.aa+=(1-alpha)*acc;
        }
        scalar water=s.water, air=offsets_.size()-water;
        if (water>SMALL) {s.uw/=water; s.aw/=water;}
        if (air>SMALL) {s.ua/=air; s.aa/=air;}
        s.water/=offsets_.size();
        return s;
    }
};
}
#endif
