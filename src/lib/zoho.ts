// Zoho CRM "Web-to-Lead" connection config, shared by both lead-capture
// forms: the project-inquiry form (src/components/ProjectInquiryForm.tsx)
// and the full consultation-request form
// (src/components/ConsultationForm.tsx).
//
// Web-to-Lead lets a plain HTML <form> POST straight to Zoho and create a
// Lead record — no backend/API route needed, which matters here since this
// site is a static export (`output: "export"`, no server runtime). This is
// intentionally the *only* file that needs editing to go live with Zoho:
//
//   1. In Zoho CRM: Setup → Developer Space → Web Forms → create a Web-to-Lead
//      form for whichever fields the sales team wants captured.
//   2. Zoho generates an HTML snippet containing a form `action` URL and a
//      few hidden inputs (`xnQsjsdp`, `xmIwtLD`, etc.) — copy those values
//      into ENABLED/POST_URL/HIDDEN_FIELDS below.
//   3. Confirm the `FIELD_NAMES` below match the actual Zoho field API names
//      shown in that generated snippet (they can vary by org config/locale).
//   4. Set ENABLED to true and redeploy.
//
// Nothing below is a real credential — these are non-secret form tokens
// Zoho embeds in public HTML by design (equivalent to a form's target URL).
export const ZOHO_WEB_TO_LEAD = {
  // Flip to true once POST_URL and HIDDEN_FIELDS are filled in from Zoho.
  // While false, both forms submit via WhatsApp instead (matches the rest
  // of the site today) and none of the fields below are used.
  enabled: false,

  // The form's POST target, e.g. "https://crm.zoho.com/crm/WebToLeadForm".
  postUrl: "",

  // Optional URL Zoho redirects to after a successful submit. Leave blank
  // to stay on the current page (the form is submitted to a hidden iframe).
  returnUrl: "",

  // Hidden tokens copied verbatim from Zoho's generated form snippet.
  hiddenFields: {
    xnQsjsdp: "",
    xmIwtLD: "",
    actionType: "TGVhZHM=", // base64 "Leads" — Zoho's default, usually unchanged
    // ldeskuid / privacy-consent hidden fields go here too, if the org's
    // form includes them (e.g. GDPR consent checkboxes require extras).
  },

  // Fixed value stamped on every lead created through this form, so sales
  // can filter "where did this come from" in Zoho.
  leadSource: "Website - Altiva",

  // Zoho's field API names for each value the form collects. Confirm these
  // against the generated snippet — some orgs rename or localize them.
  fieldNames: {
    lastName: "Last Name", // Zoho requires a Last Name on every Lead;
    // both forms send the full name here since we don't collect
    // first/last separately.
    mobile: "Mobile",
    email: "Email", // standard Zoho Lead field; ConsultationForm's optional
    // email input maps here directly when filled in.
    leadSource: "Lead Source",
    description: "Description", // every field without its own standard Zoho
    // mapping (service type, emirate/location, budget, timeline, preferred
    // contact method, notes) is folded into this as a labeled summary, so
    // nothing the client asked to capture is ever dropped even before the
    // org's real Zoho form defines matching custom fields. Once real custom
    // field API names exist (e.g. a "Service Type" picklist), add them here
    // and switch that value over from the Description fold to its own
    // named hidden input in ConsultationForm.tsx.
    company: "Company", // often required by Zoho's default Lead layout;
    // both forms send a fixed placeholder ("Individual — Website Lead")
    // unless/until the org's form is reconfigured to drop this requirement.
  },
};
