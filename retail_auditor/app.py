
import streamlit as st
import pandas as pd
from src.data_utils import load_data, get_column_mapping, load_unit_master
from src.auditor_llm import AuditorLLM
from src.prompt_manager import PromptManager, PromptTemplate, get_default_prompt

st.set_page_config(page_title="Retail Smart Auditor", layout="wide")

st.title("📦 Retail Smart Auditor")
st.markdown("""
Compare the **System's Rule-Based Verdict** against an **AI-Powered Verdict**.
""")

# Initialize prompt manager and session state
prompt_mgr = PromptManager()

if 'audit_results' not in st.session_state:
    st.session_state['audit_results'] = None

# Sidebar settings
with st.sidebar:
    st.header("⚙️ Configuration")
    st.info("System Ready")

    st.subheader("🤖 Model Selection")
    
    model_options = [
        "google/gemini-2.5-pro",
        "google/gemini-2.0-flash",
        "google/gemini-2.0-flash-lite",
        "openai/gpt-5"
    ]
    
    selected_model = st.selectbox(
        "Choose AI Model",
        model_options,
        index=0
    )
    
    st.caption(f"Active: **{selected_model}**")
    
    st.divider()
    
    # Prompt Management Section
    st.header("🎯 Prompt Management")
    
    # Get available prompts
    saved_prompts = prompt_mgr.list_prompts()
    prompt_options = ["Default (Built-in)"] + saved_prompts
    
    # Prompt selector
    selected_prompt_name = st.selectbox(
        "Select Prompt Template",
        prompt_options,
        help="Choose a prompt template for the AI auditor"
    )
    
    # Load selected prompt
    if selected_prompt_name == "Default (Built-in)":
        current_prompt = get_default_prompt()
        is_default = True
    else:
        current_prompt = prompt_mgr.get_prompt(selected_prompt_name)
        is_default = False
    
    # Show current prompt info
    if current_prompt:
        with st.expander("📝 View/Edit Current Prompt", expanded=False):
            st.caption(f"**Description:** {current_prompt.description}")
            
            # Tabs for different prompt components
            tab1, tab2, tab3 = st.tabs(["System Prompt", "Few-Shot Examples", "User Template"])
            
            with tab1:
                edited_system = st.text_area(
                    "System Prompt",
                    value=current_prompt.system_prompt,
                    height=200,
                    help="Defines the AI's role and behavior"
                )
            
            with tab2:
                edited_few_shots = st.text_area(
                    "Few-Shot Examples",
                    value=current_prompt.few_shot_examples,
                    height=300,
                    help="Example inputs and outputs to guide the AI"
                )
            
            with tab3:
                st.caption("Use {product}, {quantity}, {unit}, {price_range}, {median_context} as placeholders")
                edited_user_template = st.text_area(
                    "User Prompt Template",
                    value=current_prompt.user_prompt_template,
                    height=200,
                    help="Template for the user query (supports variable substitution)"
                )
            
            # Save prompt section
            st.divider()
            col1, col2 = st.columns([2, 1])
            
            with col1:
                new_prompt_name = st.text_input(
                    "Save As",
                    value="" if is_default else selected_prompt_name,
                    placeholder="Enter prompt name..."
                )
            
            with col2:
                save_button = st.button("💾 Save", use_container_width=True)
            
            prompt_description = st.text_input(
                "Description (optional)",
                value="" if is_default else current_prompt.description,
                placeholder="Brief description of this prompt..."
            )
            
            if save_button:
                if new_prompt_name and new_prompt_name != "Default (Built-in)":
                    new_prompt = PromptTemplate(
                        name=new_prompt_name,
                        system_prompt=edited_system,
                        few_shot_examples=edited_few_shots,
                        user_prompt_template=edited_user_template,
                        description=prompt_description
                    )
                    if prompt_mgr.save_prompt(new_prompt):
                        st.success(f"✅ Saved prompt: {new_prompt_name}")
                        st.rerun()
                    else:
                        st.error("❌ Failed to save prompt")
                else:
                    st.warning("⚠️ Please enter a valid prompt name")
            
            # Delete button (only for non-default prompts)
            if not is_default:
                if st.button("🗑️ Delete This Prompt", use_container_width=True):
                    if prompt_mgr.delete_prompt(selected_prompt_name):
                        st.success(f"✅ Deleted prompt: {selected_prompt_name}")
                        st.rerun()
                    else:
                        st.error("❌ Failed to delete prompt")
    
    # Store selected prompt in session state for use in audit
    if current_prompt:
        st.session_state['current_prompt'] = current_prompt
    else:
        st.session_state['current_prompt'] = get_default_prompt()


# Main content: Auto-load data
# Main content: Data Loading
LEADS_PATH = "data/sample_leads.csv"
UNIT_MASTER_PATH = "data/unit_master.csv"

st.header("📂 Data Source")

uploaded_file = st.file_uploader("Upload your leads CSV (Optional)", type=["csv"])

st.info("""
**📌 Note: Mandatory Columns for Processing**
The system automatically maps columns (case-insensitive), but ensure your CSV contains:
- **Offer ID**: `eto_ofr_id` (Unique identifier, used for deduplication)
- **Product**: `glcat_mcat_name` (Item category name)
- **Quantity**: `eto_ofr_qty` (Requested amount)
- **Unit**: `eto_ofr_qty_unit` (e.g., Piece, Ton, KG)
- **Business Type Flag**: `glcat_mcat_is_business_type` (1 for industrial/bulk categories)
- **Retail Threshold**: `Retail Threshold` (Rule-based limit for retail classification)
- **Median Price**: `ml_predicted_tov_pop_median` (Used for high-value detection signals)
""")

try:
    if uploaded_file is not None:
        df = load_data(uploaded_file)
        st.success(f"✅ Loaded {len(df)} rows from uploaded file.")
    else:
        df = load_data(LEADS_PATH)
        st.info(f"ℹ️ Using default sample data ({len(df)} rows). Upload a CSV to override.")

    unit_map = load_unit_master(UNIT_MASTER_PATH)
    st.success(f"✅ Loaded Unit Master.")
    
    # Display raw data preview
    with st.expander("Preview Raw Data"):
        st.dataframe(df.head())
        
    # Map columns
    col_map = get_column_mapping(df)
    
    # Remove duplicates if Offer ID exists
    offer_id_col = col_map.get('offer_id', 'eto_ofr_id')
    if offer_id_col in df.columns:
        initial_count = len(df)
        df = df.drop_duplicates(subset=[offer_id_col])
        final_count = len(df)
        if initial_count > final_count:
            st.info(f"Removed {initial_count - final_count} duplicate rows. Unique records: {final_count}")

    
    # Filter Controls
    st.subheader("Filter & Audit")
    
    # 1. Product Filter
    prod_col = col_map.get('product_name', 'glcat_mcat_name')
    if prod_col in df.columns:
        all_products = sorted(df[prod_col].astype(str).unique().tolist())
        selected_products = st.multiselect("Filter by Product Category (glcat_mcat_name)", all_products, placeholder="Select products to group audit...")
    else:
        selected_products = []
        st.warning("Product column not found for filtering.")

    # 2. Limit Control
    limit = st.number_input("Max Rows to Audit", min_value=1, max_value=1000, value=20)
    
    # 3. Process Button
    if st.button("🚀 Run Smart Audit"):
        auditor = AuditorLLM(model_name=selected_model)
        progress_bar = st.progress(0)
        results = []
        
        # Determine dataset to use
        if selected_products:
            # Filter df by selected products
            target_df = df[df[prod_col].isin(selected_products)].head(limit)
            st.info(f"Auditing top {len(target_df)} rows for selected products: {', '.join(selected_products[:3])}...")
        else:
            # Use original df
            target_df = df.head(limit)
            st.info(f"Auditing first {len(target_df)} rows from full dataset...")
        
        for i_progress, (idx, row) in enumerate(target_df.iterrows()):
            # 1. Extract Details
            # Product
            prod = row.get(col_map.get('product_name', 'glcat_mcat_name'), "Unknown")
            
            # Quantity
            qty = row.get(col_map.get('quantity', 'eto_ofr_qty'), 0)
            try:
                qty = float(qty)
            except:
                qty = 0
            
            # Unit (Use ID to lookup if possible, else text)
            unit_id = row.get('Unit ID', None)
            unit_text = row.get(col_map.get('unit', 'eto_ofr_qty_unit'), "")
            
            # For LLM: include ID for context
            if pd.notna(unit_id) and unit_id in unit_map:
                unit_display_llm = f"{unit_map[unit_id]} (ID:{int(unit_id)})"
                unit_display_table = unit_map[unit_id]  # Clean version for table
            else:
                unit_display_llm = unit_text
                unit_display_table = unit_text
            
            # Price
            price = row.get(col_map.get('price', 'eto_ofr_approx_order_val_mapp'), "N/A")
            
            # Extract median price for threshold check
            median_price_raw = row.get('ml_predicted_tov_pop_median', None)
            median_price = 0
            median_value = 0
            has_median = False
            
            if pd.notna(median_price_raw):
                try:
                    median_price = float(median_price_raw)
                    median_value = median_price * qty
                    has_median = True
                except:
                    pass
            
            # Check if median_value exceeds 10,000 threshold
            exceeds_threshold = has_median and median_value > 10000
            median_value_info = None
            
            if exceeds_threshold:
                median_value_info = f"The total value (median price ₹{median_price:,.0f} × quantity {qty}) = ₹{median_value:,.0f}, which exceeds ₹10,000."
            
            # 2. Calculate System Verdict
            # Priority A: Business Type Flag
            bus_flag_col = col_map.get('business_type_flag', 'glcat_mcat_is_business_type')
            bus_flag_val = row.get(bus_flag_col, 0)
            
            is_business_type = False
            try:
                # Check for 1, '1', 1.0
                is_business_type = float(bus_flag_val) == 1.0
            except:
                pass
            
            # Priority B logic preparations
            threshold_col = col_map.get('retail_threshold', 'Retail Threshold')
            threshold_val = row.get(threshold_col, None)

            # Calculate system verdict based on existing rules
            if is_business_type:
                sys_verdict = "Non-Retail"
                sys_reason = "MCAT is Business Type"
            else:
                # Priority B: Retail Threshold
                # Check validity of threshold
                has_threshold = False
                thresh_num = 0
                if pd.notna(threshold_val):
                    try:
                        clean_thresh = str(threshold_val).replace(',', '').strip()
                        if clean_thresh and clean_thresh.lower() not in ['#n/a', 'nan', '']:
                            thresh_num = float(clean_thresh)
                            has_threshold = True
                    except:
                        pass
                
                if has_threshold:
                    if qty >= thresh_num:
                        sys_verdict = "Non-Retail"
                        sys_reason = f"Qty {qty} >= Threshold {thresh_num}"
                    else:
                        sys_verdict = "Retail"
                        sys_reason = f"Qty {qty} < Threshold {thresh_num}"
                else:
                    # Fallback: No Threshold -> Default to Non-Retail
                    sys_verdict = "Non-Retail"
                    sys_reason = "No Threshold / Default Non-Retail"
            
            # Track high-value flag separately
            high_value_flag = "Yes ⚠️" if exceeds_threshold else "No"



            # 3. Get AI Verdict (with custom prompt if selected)
            active_prompt = st.session_state.get('current_prompt', get_default_prompt())
            
            # Use custom prompts if not default
            if active_prompt.name != "Default":
                audit = auditor.analyze_lead(
                    prod, qty, unit_display_llm, price,
                    custom_system_prompt=active_prompt.system_prompt,
                    custom_few_shots=active_prompt.few_shot_examples,
                    custom_user_template=active_prompt.user_prompt_template,
                    median_value_info=median_value_info
                )
            else:
                audit = auditor.analyze_lead(
                    prod, qty, unit_display_llm, price,
                    median_value_info=median_value_info
                )
            
            # 4. Compare
            match = (sys_verdict == audit.classification)
            
            # Format median value display
            median_display = f"₹{median_value:,.0f}" if has_median else "N/A"
            
            # Offer ID
            offer_id = row.get(col_map.get('offer_id', 'eto_ofr_id'), "N/A")

            results.append({
                "Offer ID": offer_id,
                "Product": prod,
                "Qty": f"{qty} {unit_display_table}",
                "Median Value": median_display,
                "High Value?": high_value_flag,
                "Retail Threshold": threshold_val if pd.notna(threshold_val) else "N/A",
                "System Verdict": sys_verdict,
                "System Reason": sys_reason,
                "AI Verdict": audit.classification,
                "Confidence": audit.confidence,
                "Match": "✅" if match else "❌ Discrepancy",
                "Reasoning": audit.reasoning,
            })
            
            # Fixed progress bar logic
            total_items = len(target_df)
            prog_val = (i_progress + 1) / total_items
            prog_val = min(max(prog_val, 0.0), 1.0) # Clamp
            progress_bar.progress(prog_val)
        
        # Save to session state
        st.session_state['audit_results'] = results
        st.rerun()

    # 4. Display Results (Moved outside button to persist during reruns)
    if st.session_state['audit_results'] is not None:
        res_df = pd.DataFrame(st.session_state['audit_results'])
        
        st.divider()
        st.subheader("📊 Audit Findings")
        
        col1, col2, col3 = st.columns([1, 1, 2])
        match_count = len(res_df[res_df["Match"] == "✅"])
        mismatch_count = len(res_df) - match_count
        
        col1.metric("Matches", match_count)
        col2.metric("Discrepancies", mismatch_count)
        
        # Filter toggle
        show_mismatch = st.checkbox("🔍 Show Only Discrepancies", value=False)
        
        if show_mismatch:
            final_df = res_df[res_df["Match"] != "✅"]
        else:
            final_df = res_df
            
        st.dataframe(
            final_df.style.map(
                lambda x: "background-color: #ffdbd9" if "Discrepancy" in str(x) else "",
                subset=["Match"]
            ),
            use_container_width=True,
            hide_index=True
        )
        
        # Download option
        csv = res_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            "📥 Download Full Audit CSV",
            csv,
            "audit_results.csv",
            "text/csv",
            key='download-csv'
        )
                    
except Exception as e:
    st.error(f"Error: {e}")
    st.exception(e)