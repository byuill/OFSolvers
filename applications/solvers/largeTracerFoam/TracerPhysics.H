// Individual physical spheres: SI units, no parcel weighting.
#ifndef TracerPhysics_H
#define TracerPhysics_H
#include "fvCFD.H"
#include <random>
#include <cmath>

namespace Foam
{
inline void requireTracer(bool ok, const string& message)
{
    if (!ok) FatalErrorInFunction << message << exit(FatalError);
}

inline scalar positiveTracer(const dictionary& d, const word& name, scalar fallback)
{
    scalar v=d.lookupOrDefault<scalar>(name, fallback);
    requireTracer(std::isfinite(v) && v>0, name+" must be finite and positive");
    return v;
}

class TracerDistribution
{
    word type_;
    scalar lo_, hi_, median_, sigma_;
    scalarList values_, weights_;
public:
    TracerDistribution(const dictionary& d)
    : type_(d.get<word>("type")), lo_(0), hi_(0), median_(0), sigma_(0)
    {
        if (type_=="constant") lo_=hi_=positiveTracer(d,"value",0);
        else if (type_=="uniform" || type_=="lognormal")
        {
            lo_=positiveTracer(d,"min",0); hi_=positiveTracer(d,"max",0);
            requireTracer(hi_>=lo_,"Distribution max must be >= min");
            if (type_=="lognormal")
            {
                median_=positiveTracer(d,"median",0);
                sigma_=positiveTracer(d,"sigma",0);
                requireTracer(lo_<hi_ && median_>=lo_ && median_<=hi_,
                    "Lognormal median must be within its positive min/max bounds");
            }
        }
        else if (type_=="discrete")
        {
            d.lookup("values") >> values_; d.lookup("weights") >> weights_;
            requireTracer(values_.size()>0 && values_.size()==weights_.size(),
                "Discrete values and weights must have the same nonzero length");
            scalar sum=0;
            forAll(values_,i)
            {
                requireTracer(std::isfinite(values_[i]) && values_[i]>0
                    && std::isfinite(weights_[i]) && weights_[i]>=0,
                    "Discrete values must be positive and weights nonnegative");
                sum+=weights_[i];
            }
            requireTracer(sum>0 && std::isfinite(sum),"Discrete weights must have positive finite sum");
        }
        else requireTracer(false,"Unknown distribution: "+type_);
    }
    scalar sample(std::mt19937_64& rng) const
    {
        if (type_=="constant") return lo_;
        if (type_=="uniform") return std::uniform_real_distribution<scalar>(lo_,hi_)(rng);
        if (type_=="discrete")
        {
            std::discrete_distribution<label> pick(weights_.begin(),weights_.end());
            return values_[pick(rng)];
        }
        std::lognormal_distribution<scalar> pick(std::log(median_),sigma_);
        for (label i=0;i<100000;++i)
        {
            scalar x=pick(rng); if (x>=lo_ && x<=hi_) return x;
        }
        requireTracer(false,"Cannot sample bounded lognormal; revise its bounds/sigma");
        return 0;
    }
};

struct Tracer
{
    label id;
    point x, initial;
    vector v, omega;
    scalar d, rho, volume, mass, water=1, maxDisplacement=0;
    scalar mobilized=-1, leftRegion=-1, crossedRim=-1, escaped=-1;
    bool active=true;
    bool initiallyInside=false, insideRegion=false;
    bool pairContactEligible=false;
    Tracer(label n, const point& p, scalar diameter, scalar density)
    : id(n), x(p), initial(p), v(Zero), omega(Zero), d(diameter), rho(density),
      volume(constant::mathematical::pi*pow3(diameter)/6), mass(rho*volume) {}
};

// Linearized sphere drag F = beta*(U-v), evaluated at the current slip.
// Schiller-Naumann for Re<1000, Cd=0.44 above. Includes the Stokes limit.
inline scalar tracerDrag(scalar d, scalar rho, scalar mu, scalar slip)
{
    const scalar Re=rho*d*slip/mu;
    if (Re<1000) return 3*constant::mathematical::pi*mu*d*(1+0.15*std::pow(Re,0.687));
    return 0.5*rho*0.44*(constant::mathematical::pi*sqr(d)/4)*slip;
}

// Exact frozen-drag integration, expressed using acceleration at the start of
// the step. Avoid F/beta and subtraction of large terminal-velocity terms:
// both lose precision as drag tends to zero (the ballistic limit).
inline void advanceTracer(Tracer& p, const vector& acceleration, scalar rate, scalar dt)
{
    const scalar h=rate*dt;
    scalar phi1, phi2;
    if (h<1e-3)
    {
        phi1=1+h*(-0.5+h*(1.0/6+h*(-1.0/24+h/120)));
        phi2=0.5+h*(-1.0/6+h*(1.0/24+h*(-1.0/120+h/720)));
    }
    else
    {
        phi1=-std::expm1(-h)/h;
        phi2=(1-phi1)/h;
    }
    p.x+=p.v*dt+acceleration*(sqr(dt)*phi2);
    p.v+=acceleration*(dt*phi1);
}

// Rigid-sphere contact impulse. Normal points from b toward a. Infinite-mass
// stationary wall if b==nullptr. Rotational inertia is 2/5*m*R^2.
inline void tracerImpulse(Tracer& a, Tracer* b, const vector& n, scalar e, scalar friction)
{
    const scalar ra=a.d/2, rb=b ? b->d/2 : 0;
    const scalar invA=1/a.mass, invB=b ? 1/b->mass : 0;
    const scalar invI=1/(0.4*a.mass*sqr(ra));
    const scalar invIb=b ? 1/(0.4*b->mass*sqr(rb)) : 0;
    vector rA=-ra*n, rB=rb*n;
    vector relative=a.v+(a.omega^rA);
    if (b) relative-=b->v+(b->omega^rB);
    scalar vn=relative&n;
    if (vn>=0) return;
    scalar jn=-(1+e)*vn/(invA+invB);
    vector tangent=relative-vn*n;
    scalar speed=mag(tangent);
    vector impulse=jn*n;
    if (speed>SMALL)
    {
        scalar jt=min(friction*jn,speed/(invA+invB+sqr(ra)*invI+sqr(rb)*invIb));
        impulse-=jt*tangent/speed;
    }
    a.v+=invA*impulse; a.omega+=invI*(rA^impulse);
    if (b) {b->v-=invB*impulse; b->omega-=invIb*(rB^impulse);}
}
}
#endif
