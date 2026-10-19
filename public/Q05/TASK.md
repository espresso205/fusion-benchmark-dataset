Reconstruct the geometry specified by drawing.dxf using Fusion MCP.
The only task input is drawing.dxf. Read its geometry, text, dimensions, layers,
groups and curve parameters. A read-only DXF parser is allowed. Use the units,
view and world coordinates declared in the DXF; do not fit, scale or reposition
the result. Do not read evaluator data, other tasks, reference models or services.
Save a self-contained, reopenable Fusion archive named submission.f3d in the
assigned output directory. Evaluation reopens that archive and exports its STEP.
An agent-exported STEP does not replace the required F3D submission.
