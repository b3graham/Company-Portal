"""Starter IT documents seeded once (as drafts) so each section opens with an outline
to fill in rather than a blank page. Edit or delete freely; they are ordinary docs."""

STARTER_DOCS = [
    ("policies", "Acceptable Use Policy", """# Acceptable Use Policy

**Applies to:** all staff and contractors using company systems, devices, or accounts.

## Purpose
Set expectations for how company devices, accounts, and data are used.

## Rules
- Company devices are for company work. Limited personal use is fine when it does not create risk.
- Do not install software outside the approved list without IT sign-off.
- Never share credentials. Use the password manager for every account.
- Report lost devices, suspicious email, or anything odd to IT the same day.
- Client data stays inside client-approved systems. No personal cloud storage, no personal email.

## Enforcement
Violations are handled by management. Deliberate misuse may end an engagement or employment.

## Review
Owner reviews annually or after any incident that touches this policy.
"""),
    ("policies", "Password and MFA Standard", """# Password and MFA Standard

## Requirements
- Every account that supports MFA has it turned on. Prefer an authenticator app or hardware key over SMS.
- Passwords are generated and stored in the company password manager. Minimum 16 characters where the service allows it.
- No password reuse across services. No shared accounts without a documented reason and owner.
- Break-glass admin credentials are sealed, stored offline, and checked quarterly.

## Rotation
- Rotate immediately on suspected compromise or when someone with access leaves.
- Scheduled rotation is not required for manager-generated unique passwords.

## Exceptions
Record any exception here with the reason, compensating control, and expiry date.
"""),
    ("policies", "Device Hardening Baseline", """# Device Hardening Baseline

Minimum configuration for any laptop, desktop, or phone that touches company or client data.

## All devices
- Full-disk encryption enabled (BitLocker, FileVault, or platform equivalent).
- Screen lock at 5 minutes or less. Login required on wake.
- Automatic OS and browser updates on. Patches applied within 7 days for critical issues.
- Endpoint protection installed and reporting.
- Local admin rights only where the job requires it; separate daily-use account otherwise.
- Firewall on. Unused services and ports disabled.

## Phones and tablets
- PIN or biometric lock. Remote wipe enabled through MDM or the platform account.

## Retirement
- Wipe with a documented method before resale, donation, or disposal. Record the wipe on the asset in the tracker.
"""),
    ("policies", "Data Classification and Handling", """# Data Classification and Handling

| Class | Examples | Storage | Sharing |
|---|---|---|---|
| Public | Marketing material, published reports | Anywhere | Freely |
| Internal | Procedures, internal notes, pricing | Company systems only | Staff only |
| Confidential | Client findings, scan output, credentials, contracts | Encrypted, access-controlled | Named people, need-to-know |
| Restricted | Exploit details for live client systems, PII | Encrypted, logged access | Engagement team only, client approval to move |

## Handling rules
- Label documents with their class when it is not obvious.
- Confidential and Restricted data leaves company systems only through client-approved channels.
- Delete client data at the retention date agreed in the engagement contract.
"""),
    ("runbooks", "VPN Setup", """# VPN Setup

How to connect a new device to the company VPN.

## Before you start
- You need: your VPN profile file (from IT), your MFA device, and admin rights on the device.
- Server side: note the VPN server host, port, and protocol here.

## Windows
1. Install the VPN client.
2. Import the profile file.
3. Connect. Confirm your public IP changes and internal resources resolve.

## macOS
1. Install the VPN client.
2. Import the profile file and allow the system extension.
3. Connect and verify as above.

## Phone
1. Install the client from the app store.
2. Import the profile (share it from the password manager, never by plain email).

## Troubleshooting
- Connection stalls: check the router-side VPN service is running and the WAN IP has not changed.
- DNS fails after connect: confirm the pushed DNS server responds.
- Tunnel watchdog alerts: restart the VPN server service on the router, then the client.

## Offboarding
Revoke the client certificate or profile when a device is retired or a person leaves.
"""),
    ("runbooks", "New Device Build", """# New Device Build

Checklist for bringing a new laptop or desktop into service.

1. Record it in the asset tracker: tag, serial, purchase date, cost, warranty.
2. Apply the Device Hardening Baseline (see Policies).
3. Create the user account. Separate admin account if the person needs local admin.
4. Install the standard toolset: password manager, MFA app, VPN client, endpoint protection, office suite.
5. Join to management or MDM if applicable.
6. Enable backups for the user profile.
7. Set the asset status to In service and assign it to the person.
8. Hand over with a short walkthrough of the Acceptable Use Policy.
"""),
    ("runbooks", "MFA Enrollment", """# MFA Enrollment

1. Install the authenticator app on the phone (or provision the hardware key).
2. Sign in to each service listed in Software and Licenses that supports MFA.
3. Enroll under Security settings. Save recovery codes to the password manager entry for that service.
4. Test sign-out and sign-in once.
5. Note the enrollment date on the person's onboarding checklist.

## Lost device
Use the recovery codes from the password manager to regain access, then re-enroll on the replacement device and revoke the old one.
"""),
    ("dr", "Disaster Recovery Plan", """# Disaster Recovery Plan

## Scope
Systems this plan covers and the order they come back in.

| Priority | System | RTO | RPO | Restore from | Owner |
|---|---|---|---|---|---|
| 1 | Email and identity | 4 h | 1 h | Provider | |
| 2 | Company portal (VPS) | 8 h | 24 h | Nightly tar backup | |
| 3 | File storage | 24 h | 24 h | | |
| 4 | Engagement tooling | 48 h | 24 h | | |

## Scenarios
### VPS lost or compromised
1. Provision a fresh host. Restore the latest `data/` backup.
2. Redeploy from the repository. Reissue certificates through Caddy.
3. Rotate every credential that lived on the host.

### Office or home network down
1. Confirm ISP status. Fail over to mobile hotspot for critical work.
2. VPN clients reconnect once the router is back. See the VPN runbook.

### Ransomware on a workstation
1. Disconnect the device from the network. Do not power off.
2. Follow the Incident Response steps. Rebuild from the New Device Build runbook after evidence is captured.

## Contact tree
List who to call, in order, with a backup for each role.

## Testing
Test one scenario per quarter. Record the date, what was tested, and what broke.
"""),
    ("ir", "Incident Response Plan", """# Incident Response Plan

## Severity levels
| Level | Meaning | Response |
|---|---|---|
| Critical | Client data exposed, ransomware, or a live attacker inside | All hands now. Notify affected clients within the contract window. |
| High | Compromised account or device, malware contained to one host | Lead assigned within 1 hour. Work it to closure today. |
| Medium | Phishing that got clicks, lost device with encryption, policy breach | Same business day. |
| Low | Blocked phishing, scanner noise, minor misconfiguration | Log it. Review weekly. |

## Steps
1. **Detect and log.** Open an incident in the log with severity, what was seen, and when.
2. **Contain.** Isolate the device or disable the account. Do not wipe anything yet.
3. **Preserve evidence.** Screenshots, log exports, disk image if the severity warrants it. Note who collected what and when in the timeline.
4. **Eradicate.** Remove the cause: rotate credentials, remove malware, patch, rebuild from the New Device Build runbook if trust is gone.
5. **Recover.** Restore service. Watch the affected system closely for 72 hours.
6. **Close and learn.** Fill in Lessons learned. Update the policy or runbook that would have prevented it.

## Who to call
| Role | Primary | Backup |
|---|---|---|
| Incident lead | | |
| Client contact | | |
| Legal / insurance | | |

## Notification
- Clients: per the engagement contract. Default is within 24 hours of confirming their data was involved.
- Cyber insurance: check the policy for the reporting window before touching anything the insurer may want preserved.
"""),
    ("dr", "Backup Inventory", """# Backup Inventory

What is backed up, where it goes, and when it was last proven to restore.

| System | Method | Frequency | Retention | Location | Last restore test |
|---|---|---|---|---|---|
| Company portal `data/` | tar via cron | Nightly | 30 days | VPS `/root/backups` | |
| Workstations | | | | | |
| Email | Provider retention | Continuous | | Provider | |
| Password manager | Provider + encrypted export | Monthly export | | Offline | |

## Rules
- An untested backup is not a backup. Restore something from each line at least twice a year.
- Keep at least one copy off the machine it protects and one copy offline.
"""),
]
