# Business Requirements Document (BRD)
## Auto Dealer Management System — Arya Agencies

**Version:** 0.1  
**Last Updated:** March 2025  
**Status:** Draft

---

## 1. Executive Summary

The system is a server–client application for auto dealers. Dealers run a lightweight client on their local machines; the server runs on AWS and handles data, document processing (OCR), and browser automation to push information to external portals (OEM, DMV, lenders).

---

## 2. Business Objectives

- **Centralize dealer data** (vehicles, customers, deals) in a single database.
- **Reduce manual data entry** by extracting text from uploaded documents (Tesseract OCR).
- **Automate portal submissions** by filling external web forms from database data (Playwright).
- **Keep the client lightweight** so it runs on typical dealer workstations without heavy infrastructure.

---

## 3. Stakeholders

| Role | Description |
|------|-------------|
| Dealer users | Staff at Arya Agencies (and future dealers) using the client app. |
| System / DevOps | Team managing AWS, database, and deployments. |

---

## 4. Functional Requirements

### 4.1 Client Application (Dealer Workstation)

- **FR-1** Display dealership branding (e.g. "Arya Agencies") and current date.
- **FR-2** Allow users to view and manage dealer/location data (e.g. list, add dealers).
- **FR-3** Support upload of documents (e.g. buyer's order, forms) for OCR processing.
- **FR-4** Display status of jobs (e.g. OCR in progress, completed, failed).
- **FR-5** Allow users to review and correct OCR-extracted data before it is used.
- **FR-6** Trigger "send to portal" actions that enqueue automation jobs.
- **FR-7** Basic client-side validation (required fields, formats) with no heavy business logic.

### 4.2 Server / Backend

- **FR-8** Expose REST APIs for dealers, vehicles, customers, deals, and documents.
- **FR-9** Accept document uploads, store files (e.g. S3), and create OCR jobs.
- **FR-10** Process OCR jobs (Tesseract), parse results, and persist structured data.
- **FR-11** Accept automation requests, enqueue them (Redis or SQS), and track status.
- **FR-12** Run Playwright workers that log into external portals and submit data from the database.
- **FR-13** Persist all business data in PostgreSQL with clear ownership (e.g. dealer_id) for multi-tenant use.

### 4.3 Non-Functional Requirements

- **NFR-1** Client: lightweight; minimal logic beyond validation and API calls.
- **NFR-2** Server: deployable on AWS; scalable for multiple dealers and job volume.
- **NFR-3** Data: stored in PostgreSQL; files in object storage (e.g. S3).
- **NFR-4** Security: authentication and authorization; dealer data isolated by tenant.

---

## 5. Out of Scope (Current Phase)

- Mobile apps.
- Real-time collaboration.
- Full OEM/DMV/lender portal coverage (to be expanded incrementally).

---

## 6. Success Criteria

- Dealer can add/view dealer records via the client against the live backend.
- Document upload creates an OCR job and extracted data is stored and reviewable.
- Automation jobs can be enqueued and processed by Playwright workers using DB data.
- Documentation (HLD, LLD, Technical Architecture) is maintained under the project.

---

## 7. Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | Mar 2025 | — | Initial BRD for Auto Dealer system |
