# Xero Bank Reconciliation Workflows

This document summarizes two approaches for leveraging Xero bank feeds while streamlining reconciliations across multiple clients.

## 1. Auto-reconciliation with Xero bank rules

1. Maintain a **master register** of rule logic in Excel using strict `AND` conditions that can be adapted per client.
2. Because Xero bank rules do not support `OR` logic, apply client-specific tweaks directly in the Excel register before deployment.
3. Upload each client’s rule set to Xero via the API (requires Xero developer access) or through the Xero interface.
4. Reconcile in bulk via **Accounting → Bank accounts → Reconcile → Cash Coding**, which displays all unreconciled feed transactions.
5. Let Xero pre-fill coding wherever rules match. Paste supplemental grid data (for example, cost centres) directly from Excel if needed.
6. Review the suggested matches, select all rows, and approve to complete dozens of reconciliations at once.

## 2. Hybrid workflow with custom Python logic

1. Keep the live bank feed connected so incoming transactions remain visible in **Reconcile → Bank Reconciliation** without disrupting the GL.
2. Prepare a pre-coded transaction sheet (for example, in Excel) that includes the bank account, amounts, dates, contact names, account codes, tax rates, and tracking data. Ensure the amount and date match the real feed lines for auto-matching.
3. Upload the prepared transactions as Spend/Receive Money entries:
   - **Option A — Manual**: Import the CSV via **Accounting → Bank accounts → Manage Account → Import Statement**.
   - **Option B — API/Automation**: Post transactions through the Xero API or integration platforms (Zapier/Make) by mapping the spreadsheet columns to Xero `BankTransaction` fields.
4. When the bank feed brings in live transactions, Xero will auto-suggest matches whenever the amount, date, and description align. Approve these matches (optionally using Cash Coding) to reconcile in bulk.

### Python automation considerations

- Python can orchestrate end-to-end ingestion, transformation, and upload workflows by combining modules such as `requests`, `beautifulsoup4`, `selenium`, `playwright`, `ftplib`, `paramiko`, or direct API clients depending on the external systems involved.
- Typical automation steps include authenticating to the source system, extracting and transforming transaction data, and posting the resulting bank rules or pre-coded transactions to Xero via its API.
- The architecture can follow an **Extract → Transform → Upload → Reconcile** loop, optionally leveraging OAuth-based authorization for client accounts.

### Provided helper script: `xero_reconcile.py`

- Use the included CLI to express reusable AND/OR rule logic in JSON or YAML and apply it to exported bank feed CSV files.
- Invoke it with `python xero_reconcile.py rules.yaml bank_feed.csv coded.csv --summary` to generate a Xero-ready CSV that records which rule(s) matched each transaction.
- Rules support nested `all`/`any` conditions and can populate any of the import columns (Contact, Account Code, Tax Rate, Tracking, etc.).
- Example YAML snippet:

  ```yaml
  rules:
    - name: Office supplies
      priority: 10
      match:
        all:
          - field: description
            operator: icontains
            value: staples
          - field: amount
            operator: lt
            value: 0
      set:
        contact: Staples
        account_code: 420
        tax_rate: GST on Expenses
        tracking: Admin
        reference: Office supplies
    - name: Client retainers
      match:
        any:
          - field: description
            operator: icontains
            value: retainer
          - field: reference
            operator: icontains
            value: retainer
      set:
        account_code: 200
        tax_rate: No GST
        contact: Acme Ltd
        tracking: Sales
  ```

- Add `--strict` to fail the run when transactions remain unmatched or `--unmatched unmatched.csv` to export exceptions for manual review.

## 3. Choosing an approach

- Prefer Xero’s native bank rules when rule logic can be expressed purely with `AND` conditions and when administration through Excel plus the API is manageable.
- Opt for the Python-assisted workflow when you require richer conditional logic (including `OR`) or want to integrate multiple data sources before pushing transactions into Xero.
- Both approaches retain the live bank feed for visibility, enabling rapid approval through Cash Coding once the prepared data or rules are in place.
