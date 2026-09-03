retail-auditor/
│
├── app.py                         # Streamlit entry point
│
├── data/
│   ├── sample_leads.csv           # Example input CSV
│   ├── unit_master.csv            # Unit normalization
│
├── src/
│   ├── __init__.py
│   ├── data_loader.py             # CSV loading & validation
│   ├── unit_normalizer.py         # Unit normalization logic
│   ├── profiler.py                # MCAT behavioral profiling
│   ├── scorer.py                  # Retail confidence scoring
│   ├── auditor.py                 # Final classification logic
│
├── pages/
│   ├── 1_📊_Overview.py
│   ├── 2_🔍_Lead_Auditor.py
│   ├── 3_📦_MCAT_Threshold_Auditor.py
│   ├── 4_🧪_What_If_Simulator.py
│
├── requirements.txt
└── README.md
