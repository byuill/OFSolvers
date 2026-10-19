/* SPDX-License-Identifier: GPL-3.0-or-later
 * Register native v1912 models with POROSITY as the transported phase capacity.
 * Macro pattern follows OpenCFD turbulentTransportModels (GPL-3.0-or-later).
 */
#ifndef DemTurbulence_H
#define DemTurbulence_H
#include "fvOptions.H"
#include "addToRunTimeSelectionTable.H"
#include "PhaseIncompressibleTurbulenceModel.H"
#include "incompressible/transportModel/transportModel.H"
#include "makeTurbulenceModel.H"
#include "laminarModel.H"
#include "RASModel.H"
#include "LESModel.H"
makeTurbulenceModelTypes(volScalarField,geometricOneField,incompressibleTurbulenceModel,
    PhaseIncompressibleTurbulenceModel,transportModel);
namespace Foam {typedef transportModelPhaseIncompressibleTurbulenceModel DemTurbulence;}
#endif
