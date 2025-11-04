import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import os
import time
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from bs4.element import Tag

st.set_page_config(
    page_title="Automated Usability Testing Tool",
    page_icon="🔍",
    layout="wide"
)

def run_scan(urls, progress_bar, status_text):
    options = Options()
    options.add_argument("--headless")
    driver = webdriver.Chrome(options=options)

    issues = []
    page_screenshots = []

    for i, link in enumerate(urls, start=1):
        try:
            status_text.text(f"🔎 Scanning page {i}/{len(urls)}: {link}")
            driver.get(link)
            time.sleep(2)

            soup = BeautifulSoup(driver.page_source, "html.parser")

            # Take screenshot
            screenshot_path = f"screenshot_{i}.png"
            driver.save_screenshot(screenshot_path)
            page_screenshots.append((link, screenshot_path))

            # --- Rule 1: Title check ---
            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            if not title or len(title) < 3:
                issues.append(["Missing or short title", link, "Add a descriptive page title.", "High", screenshot_path])

            # --- Rule 2: Missing h1 ---
            if not soup.find("h1"):
                issues.append(["Missing H1 heading", link, "Every page should have one <h1>.", "Medium", screenshot_path])

            # --- Rule 3: Missing viewport meta ---
            viewport_meta = soup.find("meta", attrs={"name": "viewport"})
            if not viewport_meta or not safe_get(viewport_meta, "content"):
                issues.append([
                    "Missing mobile viewport meta",
                    link,
                    "Add <meta name='viewport' content='width=device-width, initial-scale=1.0'> for mobile support.",
                    "Medium",
                    screenshot_path
                ])

            # --- Rule 4: Missing alt text for images ---
            for img in soup.find_all("img"):
                if not safe_get(img, "alt"):
                    issues.append(["Image missing alt text", link, "All images should have descriptive alt text.", "High", screenshot_path])
                    break

            # --- Rule 5: Links without href ---
            for a in soup.find_all("a"):
                if not safe_get(a, "href"):
                    issues.append(["Broken/missing href", link, "Anchor tags should have a valid href.", "Low", screenshot_path])
                    break

        except Exception as e:
            issues.append(["Page load error", link, str(e), "High", None])

        # Update progress bar
        progress_bar.progress(i / len(urls))

    driver.quit()
    status_text.text("✅ Scan completed!")
    return issues, page_screenshots


def generate_pdf_report(issues, page_screenshots, output_path="usability_report.pdf"):
    doc = SimpleDocTemplate(output_path)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Usability Testing Report", styles["Title"]), Spacer(1, 20)]

    # Screenshots
    elements.append(Paragraph("Screenshots of Tested Pages:", styles["Heading2"]))
    for link, screenshot in page_screenshots:
        elements.append(Paragraph(f"Page: {link}", styles["Normal"]))
        if os.path.exists(screenshot):
            elements.append(Image(screenshot, width=300, height=200))
        elements.append(Spacer(1, 10))

    # Issues
    elements.append(Spacer(1, 20))
    if issues:
        elements.append(Paragraph("Detected Issues:", styles["Heading2"]))
        data = [["Issue", "Page", "Description", "Severity", "Screenshot Path"]]
        for issue in issues:
            issue_name, link, desc, severity, screenshot = issue
            screenshot_str = screenshot if screenshot and os.path.exists(screenshot) else "N/A"
            data.append([issue_name, link, desc, severity, screenshot_str])

        table = Table(data, colWidths=[120, 150, 200, 80, 150])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.grey),
            ("TEXTCOLOR", (0,0), (-1,0), colors.whitesmoke),
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("BOTTOMPADDING", (0,0), (-1,0), 12),
            ("GRID", (0,0), (-1,-1), 1, colors.black)
        ]))
        elements.append(table)
    else:
        elements.append(Paragraph("No major issues found!", styles["Normal"]))

    doc.build(elements)
    return output_path

# --- Safe attribute getter ---
def safe_get(element, attribute):
    """Safely get an attribute from a BeautifulSoup Tag"""
    if isinstance(element, Tag):
        return element.get(attribute)
    return None

# --- Streamlit UI ---
st.title("Automated Usability Testing Tool")

# Step 1: Ask number of pages
page_count = st.number_input("No of scan pages", min_value=1, max_value=20, value=1)

# Step 2: Take URLs manually
urls = []
for i in range(page_count):
    url = st.text_input(f"Enter URL for page {i+1}:")
    if url:
        urls.append(url)

if st.button("Start scan"):
    if not urls:
        st.error("Please enter at least one valid URL.")
    else:
        st.info(f"Scanning {len(urls)} pages... Please wait ⏳")

        progress_bar = st.progress(0)
        status_text = st.empty()

        issues, page_screenshots = run_scan(urls, progress_bar, status_text)

        # ✅ Summary
        st.subheader("Scan Summary")
        st.write(f"*Total Pages Scanned:* {len(urls)}")
        st.write(f"*Total Issues Found:* {len(issues)}")

        severity_count = {"High": 0, "Medium": 0, "Low": 0}
        for issue in issues:
            severity_count[issue[3]] += 1

        st.markdown("*Severity Breakdown:*")
        st.write(f"- 🔴 High: {severity_count['High']} issue(s)")
        st.write(f"- 🟠 Medium: {severity_count['Medium']} issue(s)")
        st.write(f"- 🟢 Low: {severity_count['Low']} issue(s)")

        # Screenshots
        st.subheader("Screenshots of Tested Pages")
        for link, screenshot in page_screenshots:
            st.write(f"*Page:* {link}")
            if os.path.exists(screenshot):
                st.image(screenshot, caption="Screenshot", width=400)
            st.markdown("---")

        # Issues
        st.subheader("Detected Issues")
        if issues:
            for issue in issues:
                issue_name, page, desc, severity, screenshot = issue

                # Color-coded severity
                if severity == "High":
                    sev_label = "🔴 *High*"
                elif severity == "Medium":
                    sev_label = "🟠 Medium"
                else:
                    sev_label = "🟢 Low"

                st.markdown(f"*Issue:* {issue_name}")
                st.markdown(f"*Page:* {page}")
                st.markdown(f"*Description:* {desc}")
                st.markdown(f"*Severity:* {sev_label}")

                if screenshot and os.path.exists(screenshot):
                    st.image(screenshot, caption="Screenshot", width=300)

                st.markdown("---")
        else:
            st.success("No major issues found!")

        # PDF report
        report_file = generate_pdf_report(issues, page_screenshots)
        st.success(f"Report generated: {report_file}")
        with open(report_file, "rb") as f:
            st.download_button("Download Report", f, file_name="usability_report.pdf")