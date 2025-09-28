# Security Policy

## Supported Versions
We provide security fixes for all releases based on or newer than the `prod_v2` branch.  
Vulnerabilities that only affect versions older than `prod_v2` will **not** receive fixes unless the issue also affects versions that are still supported.

**Recommendation:** Always run the latest `prod_v2`-based release. If you are unsure which release you are running, check your deployment’s branch or tag, and upgrade to a supported release.

## Reporting a Vulnerability
Thank you for helping keep this project secure. Please report vulnerabilities using one of the following channels:

1. **Preferred:** Open a private issue on GitHub (create an issue and mark it as `security` or use the repository's "Security" -> "Report a vulnerability" flow).
2. **Alternative / private:** DM on Discord to ` .superintendent` (note leading dot in username).
3. **If you have gained remote code execution (RCE) or access to accounts without proper authentication:** report via **both** channels above and include the environment details listed below.

### What to include in a report
To help us triage and remediate quickly, include as much of the following as possible:
- A clear summary of the issue (one-line).
- Affected versions/branches (e.g., `main`, `prod_v2`, tag `v1.2.3`).
- Steps to reproduce (exact commands, requests, or interactions).
- Proof of concept (PoC) that demonstrates the issue — please minimize any sensitive data in PoC.
- Impact assessment (e.g., RCE, data exposure, privilege escalation).
- Environment details:
  - Python version (`python --version`)
  - MongoDB version (`mongod --version`)
  - FastAPI version (e.g., `pip show fastapi`)
  - OS and other relevant dependencies
- Any logs, stack traces, or HTTP traces (redact PII or secrets).
- Contact information so we can follow up (GitHub handle, email, or Discord username).

### Do not include
- Do not include unredacted production data, personal data, or private keys in your report.
- If PoC must include sensitive material to reproduce the issue, provide sanitized steps and offer to share sensitive artifacts over a private channel.

## Response and Disclosure Policy
- We will acknowledge receipt of your report within **5 business days**.
- Our goal is to fix critical issues (e.g., RCE, auth bypasses, data exfiltration) as quickly as possible; less critical issues will be scheduled according to severity and available resources.
- Coordinated disclosure: we prefer to coordinate fixes before public disclosure. We typically request up to **90 days** for coordinated disclosure for critical issues; this period may be extended in exceptional circumstances and will be discussed with the reporter.
- If we cannot reproduce the issue or need more information, we will request it from the reporter.

## Severity Guidance
While we make determinations on severity after triage, here are our general definitions:
- **Critical:** Remote code execution, authentication bypass, or data leakage of secrets or personal data.
- **High:** Privilege escalation, serious injection issues, or capability to significantly degrade service.
- **Medium:** Logical issues with potential for misuse or local information leakage.
- **Low:** Minor weaknesses, info-only errors, or missing hardening measures.

## Safe Harbor
If you follow this policy and act in good faith to avoid privacy violations, data destruction, or service disruption while investigating/reporting, we will not initiate legal action. Please avoid disruptive actions (e.g., exfiltrating private user data, tampering with production assets). If you're unsure whether an action is acceptable, ask first.

## Acknowledgements
We appreciate contributions from the security community. If you wish, you can indicate in your report whether you'd like to be credited for the finding and how (GitHub handle, real name, or anonymously). We will list contributors in the repository `SECURITY` or `AUTHORS` file as appropriate, with your permission.

## Contact & PGP (optional)
- GitHub: use the repository Security reporting flow or open a private issue.
- Discord: `.superintendent`

**PGP key (optional):** If you prefer encrypted communication, include your PGP public key in your report or request ours and we will provide a key fingerprint.  
(If you want to publish a PGP key, paste it here or link to a keyserver entry.)

---

## Vulnerability Report Template (copy into your issue message)
