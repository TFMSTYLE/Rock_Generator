# Rock Generator

![rock-thumb](https://github.com/user-attachments/assets/0d7f3891-4fac-4674-a291-898f672489b7)

**Author:** The French Monkey (TFMSTYLE)  
**Version:** 1.0.0  

---

## Overview

The Rock Generator procedurally creates natural, customizable rock meshes through simulated erosion, chipping, and surface deformation.  
It combines layered bisection algorithms with noise-driven displacement to achieve realistic geological formations.  
Parameters allow control over everything from base shape and anisotropic scaling to fine surface cracking, stratification, and weathering.  
Perfect for environment modeling, scattering assets, or stylized rock formations.

---

## Parameters

### Live Update
When enabled, automatically regenerates the rock whenever a parameter changes.  
Useful for real-time experimentation but may slow down for highly detailed meshes.

---

### Base Shape
Defines the primitive shape used as the base before deformation.  
Options include:

- **Cube:** Best for blocky or angular rocks.  
- **Sphere:** Produces rounded, boulder-like formations.  
- **Cylinder:** Ideal for columnar or stacked rock structures.

---

### Size
Sets the overall size of the generated rock mesh.  
All subsequent deformation scales proportionally with this value.

---

### Subdivisions
Controls the initial base mesh resolution.  
Higher values allow for more detail during erosion and noise deformation.

---

### Seed
Specifies the random seed for generation.  
Change this to produce different rock shapes with identical settings.

---

### Scale X / Scale Y / Scale Z
Applies non-uniform scaling along each axis to simulate natural anisotropy.  
Useful for flattening, stretching, or directional shaping.

---

### Chip Layers
Number of recursive erosion cuts applied to the rock surface.  
Higher values produce more irregular, fractured surfaces.

---

### Chip Radius
Determines how deep each erosion cut penetrates the mesh.  
Smaller values result in sharper and more aggressive chipping.

---

### Normal Jitter
Adds random angular variation to the orientation of erosion planes.  
Increases irregularity and breaks up repetitive patterns.

---

### Outward Bias
Controls how strongly erosion is biased toward the rock’s outer volume.  
Higher values preserve the overall core shape, while lower ones create hollowed or broken pieces.

---

### Cut Noise Freq
Adjusts the spatial frequency of noise applied to erosion planes.  
Higher values create finer variations and more chaotic surfaces.

---

### Cut Noise Amp
Controls the amplitude of noise used for erosion displacement.  
Higher values produce rougher and more uneven rock cuts.

---

### Edge Chip Strength
Determines how much the rock’s boundary edges are randomly displaced to simulate chipped or fractured borders.

---

### Edge Chip Scale
Controls the scale of the chipped edge details.  
Higher values make edge irregularities larger and more pronounced.

---

### Weathering Strength
Adds large-scale surface bulges and erosion effects to mimic long-term weathering.  
Higher values produce smoother, more eroded appearances.

---

### Weathering Frequency
Sets the scale of weathering noise patterns.  
Lower values create broad bulges, while higher values result in finer erosion.

---

### Strata Layers
Defines the number of sedimentary layers simulated through vertical sine displacement.  
Useful for creating layered sedimentary rocks.

---

### Strata Strength
Adjusts the vertical displacement intensity of stratification.  
Higher values exaggerate layering depth and wave amplitude.

---

### Crack Density
Determines the proportion of edges that receive crack deformation.  
Higher values create denser fracture patterns.

---

### Crack Depth
Controls how deeply surface cracks are indented into the mesh.  
Use with low density for subtle imperfections, or high for broken surfaces.

---

### Surface Detail Amp
Sets the amplitude of fine noise-based surface details.  
Mimics micro roughness and small surface irregularities.

---

### Surface Detail Freq
Defines the frequency of fine surface detail noise.  
Higher values produce tighter, grainier surfaces.

---

### Detail Octaves
Controls how many layers of noise are stacked for micro detail.  
Higher values increase complexity but also computation time.

---

### Adaptive Subdivision
When enabled, adds geometry only where necessary during erosion.  
Improves detail distribution and reduces unnecessary polygon density.

---

### Weld Distance
Merges nearby vertices to clean up geometry after generation.  
Helps remove small artifacts or gaps caused by noise deformation.

---

### Flat Bottom
Cuts the rock along the ground plane (Z = 0) to create a stable, flat base.  
Useful for rocks that must rest naturally on surfaces.

---

## Operators

### Generate Rock
Creates a new rock based on the current settings or regenerates the selected one if it was created by the Rock Generator.  
Applies all shape, erosion, and surface effects in sequence.

---

### Randomize Seed
Assigns a random new seed to generate a different rock shape while keeping other parameters identical.

---

### Reset Settings
Restores all parameters to their default values, keeping section visibility and live update preferences intact.

---

## Usage Notes

- Adjust **Chip Layers** and **Chip Radius** to balance surface detail and performance.  
- Use **Weathering** and **Stratification** together for natural, geological realism.  
- Enable **Flat Bottom** when placing rocks on terrain or floors.  
- Combine **Surface Detail** and **Cracks** for high-fidelity close-ups.  
- Use **Adaptive Subdivision** for efficient generation at higher chip counts.  
- Generated meshes are manifold, optimized, and ready for shading, sculpting, or scattering workflows.
