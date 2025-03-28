# -*- coding: utf-8 -*-
import streamlit as st
import requests
import os
import tempfile
import shutil
# Removed: from threading import Thread - No longer needed for main logic
from bs4 import BeautifulSoup # Requires 'beautifulsoup4' in requirements.txt
from urllib.parse import urlparse, urljoin
import subprocess
from datetime import datetime
import platform # Added for platform check in chrome path getter
import zipfile # Added for zip functionality
import io      # Added for zip functionality (BytesIO)

# --- Configuration ---

# Use a more descriptive User-Agent
HEADERS = {
    # IMPORTANT: Replace with your actual contact info/app name if deploying publicly
    'User-Agent': 'Streamlit SEC Filing Viewer App (sec-viewer-contact@example.com)'
}

# Paths for Chrome (Server-Side ONLY, if using the Chrome method)
# Ensure Chrome is installed on the server if you uncomment and use this.
CHROME_PATH_SERVER = {
    'linux': '/usr/bin/google-chrome',  # Common path on Linux
    'windows': 'C:/Program Files/Google/Chrome/Application/chrome.exe', # Adjust if needed
    'darwin': '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' # Adjust if needed
}

# --- Helper Functions ---

# Note: The "missing ScriptRunContext" warning might occasionally appear,
# potentially due to internal threading in libraries like WeasyPrint or its
# dependencies. If the app functions correctly, this warning can often be ignored.
_status_area = None
def setup_status_area():
    """Initializes the container used for status messages."""
    global _status_area
    # Ensure session state holds the container, create if needed
    if 'status_container' not in st.session_state:
        st.session_state.status_container = st.container()
    _status_area = st.session_state.status_container

def update_status(message, level="info"):
    """Updates the status area in the Streamlit app."""
    if _status_area:
        with _status_area:
            if level == "info":
                st.info(message)
            elif level == "success":
                st.success(message)
            elif level == "warning":
                st.warning(message)
            elif level == "error":
                st.error(message)
    else: # Fallback if container somehow not set up (shouldn't happen with current logic)
        print(f"STATUS ({level}): {message}") # Log to console as fallback

def get_filing_period(form, filing_date, fiscal_year_end_month, fy_adjust):
    """
    Determines the filing period label (e.g., FY23, 1Q24).
    (Copied logic from original, ensure it matches requirements)
    """
    reported_year = filing_date.year if filing_date.month > fiscal_year_end_month else filing_date.year - 1
    filing_month = filing_date.month

    if fy_adjust == "Previous Year":
        reported_year -= 1

    if form == "10-K":
        return f"FY{reported_year % 100:02d}"
    elif form == "10-Q":
        # Logic to determine quarter based on filing month relative to FY end
        if fiscal_year_end_month == 12: # Calendar Year End
             if 1 <= filing_month <= 3: quarter, year = 3, reported_year  # Filed in Q1 -> Reports Q3 of Prev FY cycle
             elif 4 <= filing_month <= 6: quarter, year = 4, reported_year  # Filed in Q2 -> Reports Q4/FY of Prev FY cycle (unlikely for 10-Q, more like 10-K)
             elif 7 <= filing_month <= 9: quarter, year = 1, reported_year + 1 # Filed in Q3 -> Reports Q1 of Current FY cycle
             elif 10 <= filing_month <= 12: quarter, year = 2, reported_year + 1 # Filed in Q4 -> Reports Q2 of Current FY cycle
             else: quarter, year = 0, 0 # Should not happen
        elif fiscal_year_end_month == 3: # March Year End
            if 4 <= filing_month <= 6: quarter, year = 4, reported_year # Filed Apr-Jun -> Reports Q4/FY of Prev FY cycle
            elif 7 <= filing_month <= 9: quarter, year = 1, reported_year + 1 # Filed Jul-Sep -> Reports Q1 of Current FY cycle
            elif 10 <= filing_month <= 12: quarter, year = 2, reported_year + 1 # Filed Oct-Dec -> Reports Q2 of Current FY cycle
            elif 1 <= filing_month <= 3: quarter, year = 3, reported_year + 1 # Filed Jan-Mar -> Reports Q3 of Current FY cycle
            else: quarter, year = 0, 0 # Should not happen
        else:
             # Generic logic attempt (may need refinement for specific FY ends)
             months_past_fy_end = (filing_date.month - fiscal_year_end_month - 1 + 12) % 12
             quarter = (months_past_fy_end // 3) + 1
             year = reported_year
             if filing_date.month > fiscal_year_end_month:
                  year += 1

        if quarter == 4:
             update_status(f"Warning: Calculated Q4 for a 10-Q filing ({filing_date.strftime('%Y-%m-%d')}). Using FY label.", level="warning")
             return f"FY{year % 100:02d}"
        elif quarter > 0:
             return f"{quarter}Q{year % 100:02d}"
        else:
            update_status(f"Error calculating period for 10-Q filed {filing_date.strftime('%Y-%m-%d')}, FYEnd: {fiscal_year_end_month}", level="error")
            return f"ERR{reported_year % 100:02d}"
    else:
        return f"Form{form}-{reported_year % 100:02d}"


def download_assets(soup, base_doc_url, temp_dir):
    """Downloads assets (images, css) linked in the HTML to a temporary directory."""
    downloaded_assets_paths = []
    for tag in soup.find_all(['img', 'link', 'script']):
        url_attr = None
        asset_rel_url = None

        if tag.name in ['img', 'script'] and tag.get('src'):
            url_attr = 'src'
            asset_rel_url = tag['src']
        elif tag.name == 'link' and tag.get('rel') == ['stylesheet'] and tag.get('href'):
            url_attr = 'href'
            asset_rel_url = tag['href']

        if not url_attr or not asset_rel_url or asset_rel_url.startswith('data:'):
            continue

        absolute_url = urljoin(base_doc_url, asset_rel_url)
        try:
            response = requests.get(absolute_url, headers=HEADERS, stream=True, timeout=20)
            response.raise_for_status()
            parsed_url = urlparse(absolute_url)
            filename = os.path.basename(parsed_url.path)
            if not filename:
                content_type = response.headers.get('content-type', '').split(';')[0]
                ext = '.css' if 'css' in content_type else '.jpg' if 'jpeg' in content_type else '.png' if 'png' in content_type else '.js' if 'javascript' in content_type else '.asset'
                filename = f"asset_{len(downloaded_assets_paths) + 1}{ext}"
            filename = "".join(c for c in filename if c.isalnum() or c in ('-', '_', '.'))[:100]
            local_path = os.path.join(temp_dir, filename)
            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192): f.write(chunk)
            tag[url_attr] = filename
            downloaded_assets_paths.append(local_path)
        except requests.exceptions.Timeout:
             update_status(f"Timeout downloading asset {asset_rel_url}", level="warning")
        except requests.exceptions.RequestException as e:
            update_status(f"Failed to download asset {asset_rel_url}: {e}", level="warning")
        except Exception as e:
            update_status(f"Error processing asset {asset_rel_url}: {e}", level="warning")
    return downloaded_assets_paths

def convert_to_pdf_weasyprint(html_path, pdf_base_name, temp_dir):
    """Converts HTML to PDF using WeasyPrint."""
    try:
        from weasyprint import HTML, CSS
        from weasyprint.fonts import FontConfiguration
        pdf_filename = f"{pdf_base_name}.pdf"
        pdf_path = os.path.join(temp_dir, pdf_filename)
        update_status(f"Converting to PDF (WeasyPrint): {pdf_filename} ...", level="info")
        html_obj = HTML(filename=html_path)
        css = CSS(string='''
            @page { size: A4; margin: 1.5cm; } body { font-family: sans-serif; line-height: 1.4; word-wrap: break-word; }
            table { border-collapse: collapse; width: 100%; margin-bottom: 1em; } th, td { border: 1px solid #ddd; padding: 4px; text-align: left; vertical-align: top; }
            th { background-color: #f2f2f2; } img { max-width: 100%; height: auto; vertical-align: middle; }
            h1, h2, h3, h4, h5, h6 { page-break-after: avoid; } table, figure { page-break-inside: avoid; } tr, li { page-break-inside: avoid; }
        ''')
        font_config = FontConfiguration()
        html_obj.write_pdf(pdf_path, stylesheets=[css], font_config=font_config)
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            update_status(f"PDF created: {pdf_filename}", level="success")
            return pdf_path
        else:
            update_status("WeasyPrint conversion failed - no output file or file is empty", level="error")
            if os.path.exists(pdf_path): os.remove(pdf_path)
            return None
    except ImportError:
        update_status("WeasyPrint not installed.", level="error")
        st.error("PDF Conversion Error: Install WeasyPrint (`pip install WeasyPrint`) and its system dependencies (Pango, Cairo, etc.).")
        return None
    except Exception as e:
        update_status(f"Error during WeasyPrint PDF conversion: {str(e)}", level="error")
        st.exception(e)
        return None

# --- (Keep commented out Chrome conversion code if desired) ---
# def get_chrome_path_server(): ...
# def convert_to_pdf_chrome(...): ...
# ---

def download_and_process(doc_url, cik, form, date_str, accession, period, ticker, cleanup_temp_files):
    """Downloads a single filing, its assets, converts to PDF, and optionally cleans up."""
    temp_dir_filing = tempfile.mkdtemp(prefix=f"sec_{cik}_{accession}_")
    html_path = None; assets_paths = []; pdf_path_final = None
    try:
        update_status(f"Processing {form} ({period}) from {date_str}...", level="info")
        response = requests.get(doc_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        try: decoded_text = response.content.decode('utf-8')
        except UnicodeDecodeError:
            try: decoded_text = response.content.decode('iso-8859-1'); update_status(f"Decoded {doc_url} using iso-8859-1", level="info")
            except UnicodeDecodeError: decoded_text = response.content.decode('cp1252', errors='replace'); update_status(f"Decoded {doc_url} using cp1252 (replacements)", level="warning")
        replacements = { "â€": "\"", "â€œ": "\"", "â€™": "'", "â€˜": "'", "â€“": "-", "â€”": "-" }
        for wrong, correct in replacements.items(): decoded_text = decoded_text.replace(wrong, correct)
        soup = BeautifulSoup(decoded_text, 'html.parser')
        meta_charset = soup.find('meta', charset=True)
        if not meta_charset:
             meta = soup.new_tag('meta', charset='UTF-8')
             if soup.head: soup.head.insert(0, meta)
             else: head = soup.new_tag('head'); head.append(meta); soup.insert(0, head) # Simplified head creation
        elif meta_charset['charset'].lower() != 'utf-8': meta_charset['charset'] = 'UTF-8'
        assets_paths = download_assets(soup, doc_url, temp_dir_filing)
        html_filename = f"{cik}_{form}_{date_str}_{accession}.html"
        html_path = os.path.join(temp_dir_filing, html_filename)
        with open(html_path, 'w', encoding='utf-8') as f: f.write(str(soup))
        pdf_base_name = f"{ticker}_{period}" if ticker else f"{cik}_{period}"
        pdf_path_temp = convert_to_pdf_weasyprint(html_path, pdf_base_name, temp_dir_filing)
        if pdf_path_temp: pdf_path_final = pdf_path_temp
        return pdf_path_final
    except requests.exceptions.Timeout: update_status(f"Timeout downloading main HTML {doc_url}", level="error"); return None
    except requests.exceptions.RequestException as e: update_status(f"Network error downloading {doc_url}: {e}", level="error"); return None
    except Exception as e: update_status(f"Error processing filing {accession}: {e}", level="error"); st.exception(e); return None
    finally:
        should_cleanup = cleanup_temp_files or (pdf_path_final is None)
        if should_cleanup:
            try:
                if temp_dir_filing and os.path.exists(temp_dir_filing): shutil.rmtree(temp_dir_filing, ignore_errors=True)
            except Exception as e: update_status(f"Error during cleanup for {accession}: {e}", level="warning")
        # No message if temp files kept intentionally via cleanup=False

def process_filings_for_cik(cik, ticker, fiscal_year_end_month, fy_adjust, cleanup_temp_files):
    """Fetches filing list and processes 10-K/10-Q forms."""
    parent_temp_dir = tempfile.mkdtemp(prefix="sec_pdfs_run_")
    generated_pdf_final_paths = []
    try:
        cik_padded = cik.zfill(10)
        base_url = f"https://www.sec.gov/Archives/edgar/data/{cik_padded}/"
        submissions_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
        update_status(f"Fetching filing list for CIK {cik_padded}...", level="info")
        response = requests.get(submissions_url, headers=HEADERS, timeout=20)
        response.raise_for_status(); data = response.json()
        if 'filings' not in data or 'recent' not in data['filings']:
             update_status(f"No recent filings found for CIK {cik_padded}.", level="error"); return [], None
        filings = data['filings']['recent']
        company_name = data.get('name', f"CIK {cik_padded}")
        st.subheader(f"Processing Filings for: {company_name}")
        if not ticker and data.get('tickers') and isinstance(data['tickers'], list) and len(data['tickers']) > 0:
            ticker = data['tickers'][0]; update_status(f"Using ticker '{ticker}' from SEC data.", level="info")
        limit_counter = 0; max_filings_to_process = 20 # Limit processing per run
        if not all(key in filings for key in ['form', 'filingDate', 'accessionNumber', 'primaryDocument']):
            update_status("Filings data missing expected keys.", level="error"); return [], None
        for idx, form in enumerate(filings['form']):
            if idx >= len(filings['filingDate']) or idx >= len(filings['accessionNumber']) or idx >= len(filings['primaryDocument']): continue
            if limit_counter >= max_filings_to_process: update_status(f"Reached processing limit ({max_filings_to_process}).", level="warning"); break
            if form in ['10-K', '10-Q']:
                filing_date_str = filings['filingDate'][idx]
                try: filing_date = datetime.strptime(filing_date_str, "%Y-%m-%d")
                except ValueError: update_status(f"Invalid date format: {filing_date_str}", level="warning"); continue
                period = get_filing_period(form, filing_date, fiscal_year_end_month, fy_adjust)
                if period.startswith("ERR"): update_status(f"Skipping {form} from {filing_date_str} (period error).", level="warning"); continue
                try: # Optional: Skip older filings
                    year_digits = ''.join(filter(str.isdigit, period[-2:]))
                    reported_year_check = int(year_digits) + 2000
                    if reported_year_check < 2017: update_status(f"Skipping {form} ({period}) - Older than FY17.", level="info"); continue
                except ValueError: update_status(f"Could not parse year from period '{period}'.", level="warning")
                accession = filings['accessionNumber'][idx].replace('-', '')
                doc_file = filings['primaryDocument'][idx]
                if not doc_file or '..' in doc_file or '/' in doc_file or '\\' in doc_file:
                    update_status(f"Invalid doc name '{doc_file}' for {accession}.", level="warning"); continue
                doc_url = f"{base_url}{accession}/{doc_file}"
                pdf_path_temp = download_and_process(doc_url, cik_padded, form, filing_date_str, accession, period, ticker, cleanup_temp_files)
                if pdf_path_temp and os.path.exists(pdf_path_temp):
                    try:
                        final_pdf_name = os.path.basename(pdf_path_temp)
                        final_pdf_path = os.path.join(parent_temp_dir, final_pdf_name)
                        counter = 1
                        while os.path.exists(final_pdf_path): # Avoid overwrites
                             name, ext = os.path.splitext(final_pdf_name); final_pdf_path = os.path.join(parent_temp_dir, f"{name}_{counter}{ext}"); counter += 1
                        shutil.move(pdf_path_temp, final_pdf_path)
                        generated_pdf_final_paths.append(final_pdf_path)
                        limit_counter += 1
                        source_temp_dir = os.path.dirname(pdf_path_temp)
                        if cleanup_temp_files and os.path.exists(source_temp_dir):
                             try: shutil.rmtree(source_temp_dir, ignore_errors=True)
                             except Exception: pass
                    except Exception as move_err: update_status(f"Error moving PDF {os.path.basename(pdf_path_temp)}: {move_err}", level="error")
    except requests.exceptions.Timeout: update_status(f"Timeout fetching submission data for CIK {cik}", level="error")
    except requests.exceptions.RequestException as e: update_status(f"Network error fetching submission data: {e}", level="error")
    except KeyError as e: update_status(f"Data parsing error (KeyError): {e}.", level="error")
    except Exception as e: update_status(f"Unexpected error: {e}", level="error"); st.exception(e)
    if not generated_pdf_final_paths and os.path.exists(parent_temp_dir):
         shutil.rmtree(parent_temp_dir, ignore_errors=True) # Clean up parent dir if no files were successfully processed
    # Return list of final PDF paths and the parent directory they reside in
    return generated_pdf_final_paths, parent_temp_dir if generated_pdf_final_paths else None

# --- Streamlit UI ---

st.set_page_config(page_title="SEC Filing Viewer", layout="wide")
st.title("SEC Filing Viewer & PDF Converter")
st.markdown("Fetches recent 10-K and 10-Q filings, converts to PDF.")
st.warning("Requires WeasyPrint (`pip install weasyprint`) + system dependencies (Pango, Cairo, etc.).", icon="⚠️")
st.info("PDFs are generated on the server. Use download buttons to save them via your browser (typically to your 'Downloads' folder). Use 'Download All as ZIP' for convenience.", icon="ℹ️")
st.markdown("---")

# Input Columns
col1, col2 = st.columns(2)
with col1:
    cik_input = st.text_input("Company CIK:", key="cik", placeholder="e.g., 1049502")
    ticker_input = st.text_input("Ticker (Optional, for PDF filename):", key="ticker", placeholder="e.g., MRNA")
    cleanup_input = st.checkbox("Delete intermediate HTML/Asset files", value=True, key="cleanup", help="Delete temporary HTML/CSS/images after PDF generation.")
with col2:
    months = [datetime(2000, i, 1).strftime('%B') for i in range(1, 13)]
    default_month_name = "December"; default_month_index = months.index(default_month_name) if default_month_name in months else len(months) - 1
    fy_month_name_input = st.selectbox("Company Fiscal Year-End Month:", months, index=default_month_index, key="fy_month")
    st.session_state.fy_month_input = months.index(fy_month_name_input) + 1 # Store month number (1-12)
    fy_adjust_input = st.selectbox("Fiscal Year Basis:", ["Same Year", "Previous Year"], index=0, key="fy_adjust", help="'Previous Year' often used if FY ends Jan-Mar.")
st.markdown("---")

# Placeholder for status messages
if 'status_container' not in st.session_state: st.session_state.status_container = st.container()

# Button to trigger processing
if st.button("Fetch and Convert Filings", key="fetch_button"):
    generated_files = [] # List to hold paths of generated PDFs
    pdf_parent_dir = None # Directory containing the final PDFs
    st.session_state.status_container.empty() # Clear previous messages
    with st.session_state.status_container:
        setup_status_area() # Connects _status_area to the container
        if not cik_input or not cik_input.isdigit():
            update_status("CIK must be a non-empty number.", level="error")
        else:
            with st.spinner(f"Processing filings for CIK {cik_input}..."):
                fy_month_to_use = st.session_state.get('fy_month_input', 12) # Retrieve month
                # Process filings returns list of paths and the directory they are in
                generated_files, pdf_parent_dir = process_filings_for_cik(
                    cik_input.strip(), ticker_input.strip().upper(),
                    fy_month_to_use, fy_adjust_input, cleanup_input
                )
            # Final status message after spinner
            if not generated_files: update_status("No PDF files generated or error occurred.", level="warning")
            else: update_status(f"Generated {len(generated_files)} PDF file(s). Ready for download.", level="success")

    # Display download buttons only if files were generated successfully
    if generated_files and pdf_parent_dir:
        st.markdown("---")
        st.subheader("Download Options")
        st.markdown("Click buttons to download files via your browser.")

        # --- Create ZIP file in memory ---
        zip_buffer = io.BytesIO()
        # Create a relevant zip filename
        zip_base_name = ticker_input.strip().upper() if ticker_input else cik_input.strip()
        zip_filename = f"{zip_base_name}_SEC_Filings_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
        try:
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for pdf_path in generated_files:
                    # Ensure file exists before adding
                    if pdf_path and os.path.exists(pdf_path):
                        # Add file to zip using its base name (not the full temp path)
                        zip_file.write(pdf_path, arcname=os.path.basename(pdf_path))
            zip_buffer.seek(0) # Rewind buffer pointer

            # --- Add Download Button for ZIP ---
            st.download_button(
                 label=f"Download All ({len(generated_files)}) as ZIP",
                 data=zip_buffer, # Use the buffer directly
                 file_name=zip_filename,
                 mime="application/zip",
                 key="dl_zip"
            )
            # Add info about zip creation only if successful
            with st.session_state.status_container:
                update_status(f"Created {zip_filename} bundle for download.", level="info")
        except Exception as zip_err:
            # Display error if zip creation fails
            with st.session_state.status_container:
                update_status(f"Error creating ZIP file: {zip_err}", level="error")


        # --- Offer Individual Downloads ---
        st.markdown("---") # Separator
        st.subheader("Individual PDF Downloads:")
        # Dynamically adjust columns based on number of files
        num_cols = min(len(generated_files), 4) if generated_files else 1
        cols = st.columns(num_cols)
        col_idx = 0
        # Store info needed for download buttons
        st.session_state.downloadable_files_info = {os.path.basename(p): p for p in generated_files if p and os.path.exists(p)}

        # Create buttons
        for pdf_filename, pdf_path in st.session_state.downloadable_files_info.items():
            try:
                # Read file bytes needed for the button
                with open(pdf_path, "rb") as fp: pdf_bytes = fp.read()
                # Place button in the next available column
                with cols[col_idx % num_cols]:
                    st.download_button(
                        label=f"Download {pdf_filename}", data=pdf_bytes, file_name=pdf_filename,
                        mime="application/pdf", key=f"dl_{pdf_filename}" # Unique key per button
                    )
                    col_idx += 1
            except Exception as e:
                 # Report error if file can't be read for download
                 with st.session_state.status_container: update_status(f"Error reading {pdf_filename} for download: {e}", level="error")
                 # Optionally disable or hide button placeholder for this file
                 with cols[col_idx % num_cols]: st.error(f"Error for {pdf_filename}")
                 col_idx += 1

        # --- Cleanup Info ---
        # Store the parent directory path if needed for manual cleanup later (optional)
        st.session_state.pdf_parent_dir = pdf_parent_dir
        # Optionally display the temp dir path (useful for debugging if cleanup is off)
        # if pdf_parent_dir and not cleanup_input:
        #    st.caption(f"Generated files temporarily stored in: {pdf_parent_dir}")


# Footer
st.markdown("---")
st.caption("SEC EDGAR data via SEC APIs. PDF Conversion via WeasyPrint. Ensure User-Agent is set appropriately.")

# Optional manual cleanup button (consider implications before enabling)
# if 'pdf_parent_dir' in st.session_state and st.session_state.pdf_parent_dir:
#     if st.button("Clean Up Temporary Files from Last Run"):
#         parent_dir_to_clean = st.session_state.pdf_parent_dir
#         try:
#             if os.path.exists(parent_dir_to_clean):
#                 shutil.rmtree(parent_dir_to_clean, ignore_errors=True)
#                 st.toast(f"Attempted cleanup of: {parent_dir_to_clean}")
#                 del st.session_state.pdf_parent_dir # Clear from state after cleanup
#                 if 'downloadable_files_info' in st.session_state: del st.session_state.downloadable_files_info # Clear file list
#             else:
#                 st.toast("Temporary directory already removed or path invalid.")
#         except Exception as clean_err:
#             st.error(f"Error during manual cleanup: {clean_err}")