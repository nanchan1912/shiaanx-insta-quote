# Feature Processing Pipeline Integration

This document describes the feature processing pipeline integration that automatically analyzes STEP files and populates the quote form with extracted geometric features and calculated prices.

## Overview

When you upload a STEP file in `quote_demo_gui.html`, the system now:

1. **Uploads the file** to a Python backend server
2. **Runs the feature processing pipeline** which:
   - Extracts geometric features (bores, holes, pockets, bosses, etc.)
   - Clusters and classifies features by type
   - Selects manufacturing processes
   - Plans setups and selects tools
   - Calculates machining parameters
   - Generates a detailed quote
3. **Populates the quote form** with extracted values:
   - Material mass and dimensions
   - Machining parameters (cycle time, step count, operations)
   - Feature counts and complexity weighting
   - Cost calculations

## Architecture

```
┌─────────────────────┐      HTTP POST       ┌─────────────────────────┐
│  quote_demo_gui.html │ ───────────────────> │  quote_pipeline_server  │
│  (Frontend)         │   STEP file upload   │  (Flask Backend)        │
└─────────────────────┘                      └─────────────────────────┘
                                                        │
                                                        │ subprocess
                                                        ▼
                                               ┌─────────────────────────┐
                                               │  Feature Pipeline       │
                                               │  (PythonOCC scripts)    │
                                               │                         │
                                               │  1. extract_features.py │
                                               │  2. cluster_features.py │
                                               │  3. classify_features.py│
                                               │  4. process_selection.py│
                                               │  5. setup_planning.py  │
                                               │  6. tool_selection.py  │
                                               │  7. parameter_calc.py  │
                                               │  8. quote_estimation.py│
                                               └─────────────────────────┘
                                                        │
                                                        │ JSON output
                                                        ▼
                                               ┌─────────────────────────┐
                                               │  Quote JSON with        │
                                               │  - Derived context      │
                                               │  - Line items           │
                                               │  - Category totals      │
                                               └─────────────────────────┘
```

## Setup Instructions

### 1. Install Server Dependencies

```bash
pip install -r requirements-server.txt
```

### 2. Start the Pipeline Server

```bash
python quote_pipeline_server.py
```

The server will start on `http://localhost:5000`.

### 3. Open the Quote GUI

Open `quote_demo_gui.html` in a browser. You can use a simple HTTP server:

```bash
# Using Python
python -m http.server 8080

# Or using Node.js npx
npx serve .
```

Then navigate to `http://localhost:8080/quote_demo_gui.html`

## Feature Processing Pipeline Details

### Step 1: Feature Extraction (`extract_features.py`)
- Reads STEP file using PythonOCC
- Extracts topology (faces, edges, vertices)
- Calculates bounding box and mass properties
- Detects cylindrical faces (holes/bores)
- Analyzes surface types and face adjacency

### Step 2: Feature Clustering (`cluster_features.py`)
- Builds face adjacency graph
- Identifies feature seed faces
- Clusters related faces into manufacturing features
- Calculates feature axes, depths, and radii

### Step 3: Feature Classification (`classify_features.py`)
- Classifies clusters by type:
  - `through_hole` / `through_hole_angled`
  - `blind_hole` / `blind_hole_angled`
  - `counterbore` / `counterbore_angled`
  - `large_bore` / `large_bore_angled`
  - `boss` / `boss_angled`
  - `slot` / `slot_angled`
  - `pocket` / `pocket_angled`
  - `planar_face`, `background`
- Assigns confidence levels

### Step 4: Process Selection (`process_selection.py`)
- Maps features to manufacturing operations
- Selects machines (milling, turning, grinding)
- Generates operation sequences

### Step 5: Setup Planning (`setup_planning.py`)
- Groups features into setups
- Determines setup orientations
- Plans fixture rotations

### Step 6: Tool Selection (`tool_selection.py`)
- Selects appropriate tools from database
- Matches tool geometry to features
- Handles tool substitutions

### Step 7: Parameter Calculation (`parameter_calculation.py`)
- Calculates cutting parameters (RPM, feed, speeds)
- Estimates cycle times per operation
- Applies RPM caps and optimizations

### Step 8: Quote Generation (`quote_estimation.py`)
- Aggregates all data into line items
- Calculates costs by category:
  - Material (stock mass × price/kg)
  - Machining (cycle time × rate + setups)
  - CAM Programming (complexity-based)
  - Inspection (setup + time-based)
  - Post-processing (finishes)
  - Logistics (packaging + shipping)
- Applies commercial adjustments
- Generates final quote

## Data Mapping to Quote Form

The pipeline output is mapped to the following form fields:

| Pipeline Output | Form Field | Formula Impact |
|-----------------|------------|----------------|
| `stock.stock_mass_kg` | `stockMassKg` | Material Cost |
| `stock.part_mass_kg` | `partMassKg` | Display only |
| `stock.removed_mass_kg` | `removedMassKg` | Display only |
| `stock.price_per_kg` | `materialRate` | Material Cost |
| `stock.stock_dims_mm.x/y/z` | `stockX/Y/Z` | Display only |
| `part.setup_count` | `setupCount` | Setup Cost, Programming, Inspection |
| `processes.milling_3axis.cycle_time_min` | `cycleTimeMin` | Machining Cost |
| `processes.milling_3axis.step_count` | `stepCount` | Display only |
| `processes.milling_3axis.operations.face_mill` | `faceMillCount` | Display only |
| `processes.milling_3axis.operations.circular_interp` | `circularInterpCount` | Display only |
| `processes.milling_3axis.operations.contour_mill` | `contourMillCount` | Display only |
| `feature_count` | `featureCount` | Programming, Inspection |
| `weighted_features` | `weightedFeatures` | Programming Cost |
| `unique_operations` | `uniqueOperations` | Programming Cost |

All formulas in the quote form remain unchanged - the pipeline simply provides real values instead of defaults.

## Fallback Behavior

If the pipeline server is not running:
1. The frontend will attempt to connect to `http://localhost:5000`
2. If connection fails, it falls back to default/demo values
3. A message is displayed: "Using default values - pipeline unavailable"

To use the pipeline, ensure the server is running before uploading STEP files.

## Customization

### Changing the Material
Select a material from the dropdown before uploading a STEP file. The material selection is passed to the pipeline and affects:
- Material density and mass calculations
- Price per kg
- Machining parameters (speeds/feeds)

### RFQ Options
The pipeline supports RFQ (Request for Quote) JSON with:
- Surface finishes (plating, anodizing, etc.)
- Secondary operations (tapping, reaming, welding)
- Inspection level (manual/CMM)
- Packaging and shipping preferences

These can be added to the frontend and passed to the backend.

## Troubleshooting

### "Pipeline server not available"
- Ensure `quote_pipeline_server.py` is running
- Check that port 5000 is not blocked
- Verify Flask and flask-cors are installed

### "OCC not found" errors
- The pipeline requires PythonOCC (OpenCASCADE)
- Set `INSTA_QUOTE_OCC_PYTHON` environment variable to point to a Python with OCC installed
- Common location: `C:\Users\<User>\miniconda3\envs\occ\python.exe`

### Processing timeouts
- Large STEP files may take longer to process
- The server has a 120-second timeout per pipeline step
- Complex parts with many features may need more time

## Files Added/Modified

### New Files
- `quote_pipeline_server.py` - Flask backend server
- `requirements-server.txt` - Python dependencies
- `README-FEATURE-PIPELINE.md` - This documentation

### Modified Files
- `quote_demo_gui.html` - Added pipeline integration functions:
  - `processStepFileWithPipeline()` - Uploads file to backend
  - `updateStateFromPipeline()` - Maps pipeline output to state
  - `runFeatureProcessingPipeline()` - Orchestrates the process
  - Modified `setCad()` to trigger pipeline

## API Endpoints

### POST /process
Upload a STEP file and get processed quote data.

**Request:**
- `file`: STEP file (multipart/form-data)
- `material`: Material name (optional, default: 'mild_steel')
- `qty`: Quantity (optional, default: 1)

**Response:**
```json
{
  "success": true,
  "state": { /* Mapped state for quote.html */ },
  "quote": { /* Full quote JSON */ }
}
```

### GET /health
Health check endpoint.

**Response:**
```json
{
  "status": "ok",
  "pipeline_dir": "..."
}
```

## Future Enhancements

- Add RFQ JSON editor to the frontend
- Support batch processing of multiple STEP files
- Cache processed results for previously uploaded files
- Add progress indicators for long-running pipeline steps
- Support IGES files in addition to STEP
