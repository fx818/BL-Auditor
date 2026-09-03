
import pandas as pd
import io

def load_data(file_or_path):
    """
    Loads data from a file path or file-like object (uploaded file).
    """
    try:
        if isinstance(file_or_path, str):
            df = pd.read_csv(file_or_path)
        else:
            df = pd.read_csv(file_or_path)
        
        # Basic cleaning
        df.columns = [c.strip() for c in df.columns]
        return df
    except Exception as e:
        raise ValueError(f"Error loading data: {e}")

def get_column_mapping(df):
    """
    Attempts to automatically map columns to internal standard names.
    Returns a dictionary of {standard_name: actual_column_name}.
    It is case-insensitive and robust to spaces vs underscores.
    """
    mapping = {}
    
    # Store original columns and their normalized versions
    # Normalized = lowercase, spaces replaced with underscores, stripped
    actual_cols = df.columns.tolist()
    norm_to_orig = {c.lower().strip().replace(' ', '_'): c for c in actual_cols}
    
    # Simple candidate list (logic will handle casing/formatting variations)
    candidates = {
        'product_name': ['glcat_mcat_name', 'product_name', 'item'],
        'quantity': ['eto_ofr_qty', 'quantity', 'qty'],
        'unit': ['eto_ofr_qty_unit', 'unit'],
        'price': ['eto_ofr_approx_order_val_mapp', 'price', 'value', 'amount'],
        'system_verdict': ['retail_flag', 'is_retail', 'retail_status'],
        'business_type_flag': ['glcat_mcat_is_business_type', 'is_business_type', 'business_flag'],
        'offer_id': ['eto_ofr_id', 'offer_id', 'id', 'eto_ofr_display_id'],
        'retail_threshold': ['retail_threshold', 'threshold', 'retail_limit']
    }
    
    for std_col, possible_names in candidates.items():
        for cand in possible_names:
            norm_cand = cand.lower().strip().replace(' ', '_')
            if norm_cand in norm_to_orig:
                mapping[std_col] = norm_to_orig[norm_cand]
                break
    
    return mapping

def load_unit_master(path="C:\\Users\\imart\\retail-auditor\\retailauditor\\retail_auditor\\data\\unit_master.csv"):
    """
    Loads the unit master CSV and returns a dictionary mapping ID -> Display Name.
    """
    try:
        df = pd.read_csv(path)
        # Using columns: gl_unit_id, unit_display_name
        unique_units = df[['gl_unit_id', 'unit_display_name']].drop_duplicates()
        return dict(zip(unique_units['gl_unit_id'], unique_units['unit_display_name']))
    except Exception as e:
        print(f"Warning: Could not load unit master: {e}")
        return {}
