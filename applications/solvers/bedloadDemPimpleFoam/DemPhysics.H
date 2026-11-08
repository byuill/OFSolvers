/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef DemPhysics_H
#define DemPhysics_H
#include "fvCFD.H"
#include <cmath>
#include <sstream>
#include <iomanip>
namespace Foam
{
inline void requireDem(bool ok,const string& message)
{if(!ok) FatalErrorInFunction<<message<<exit(FatalError);}
inline string demNumber(scalar x)
{std::ostringstream s;s<<std::setprecision(17)<<x;return s.str();}
inline scalar demPositive(const dictionary& d,const word& key,scalar fallback)
{
    scalar x=d.lookupOrDefault<scalar>(key,fallback);
    requireDem(std::isfinite(x)&&x>0,key+" must be finite and positive");return x;
}
// Di Felice (1994), Int. J. Multiphase Flow 20:153-159. Intrinsic slip,
// Rep=epsilon*rho*d*|U-v|/mu; beta multiplies the intrinsic slip velocity.
// The algebraic expansion retains the finite Stokes limit at zero slip.
inline scalar demDrag(scalar d,scalar rho,scalar nu,scalar speed,scalar eps,const word& model)
{
    const scalar pi=constant::mathematical::pi, mu=rho*nu;
    if(model=="SchillerNaumann")
    {
        scalar re=d*speed/nu;
        return re<1000 ? 3*pi*mu*d*(1+.15*std::pow(re,.687))
            : .5*rho*.44*pi*sqr(d)*speed/4;
    }
    scalar re=eps*d*speed/nu;
    scalar chi=3.7-.65*std::exp(-.5*sqr(1.5-std::log10(max(re,scalar(1e-30)))));
    scalar cdTimesSlip=.3969*speed+6.048*std::sqrt(speed*nu/(eps*d))+23.04*nu/(eps*d);
    return .5*rho*pi*sqr(d)/4*cdTimesSlip*std::pow(eps,2-chi);
}
}
#endif
