## 1. Module: `letter_approval`

---

### 1.1 Module Responsibility

`letter_approval` is an extension module for the Letter Writer system that introduces an approvals-governed workflow on top of `letter_writer` and `letter_hr`. It integrates with Odoo’s `approvals` application to provide controlled issuance, controlled download, and role-based access to letters.

This module contributes five primary capabilities:

- Approval-category enhancements specific to letters (including an “Addressed To” policy and live reconciliation of approver lines on open requests when category configuration changes).
- Approval-request enrichment (template selection mechanics, additional request metadata, linked letters, role flags, and controlled visibility of downloadable letters).
- Letter workflow governance (optional approvals per letter, status transitions, submit/reject operations, download entitlements, and reset-to-draft safety).
- UI enforcement (view extensions on approvals and letters, domain restrictions, conditional button visibility, and statusbar integration).
- Security hardening (override of permissive base access, new access matrices, record rules, and group implied access).

In the larger architecture:

- `letter_base` provides placeholder replacement scaffolding.
- `letter_hr` provides HR context, delivery method, and HR placeholder resolution.
- `letter_approval` provides lifecycle governance and access control around issuance and download.

---

### 1.2 Before vs After Installing `letter_approval`

---

#### 1.2.1 Before Installing `letter_approval` (Core + HR Only)

With `letter_base`, `letter_writer`, and `letter_hr`:

- Templates can be authored and classified (base or hr).
- Letters can be created directly from templates.
- `replaced_content` is computed using base + HR placeholder logic.
- Export actions (PDF/DOCX) are restricted mainly via HR delivery method rules (digital vs physical), but issuance is not governed by approvals.

Result: HR letters are functional, but there is no approvals-governed issuance, no sequencing/parallel-approval enforcement, no “issue first, download later” contract, and no approval-driven security model.

---

#### 1.2.2 After Installing `letter_approval`

After installing `letter_approval`:

- A dedicated approvals workflow can govern letter issuance when `has_approval_request` is enabled on the letter.
- Approval Categories gain:
  - an “Addressed To” policy field
  - approver-line reconciliation logic for open Letter Approval requests

- Approval Requests gain:
  - template selection restrictions for request owners vs approvers/managers
  - linked letters pages (with different visibility rules per role)
  - filtered “Digital Letters” view for normal users

- Letters gain:
  - approval linkage and a stronger status model (`draft`, `pending`, `issued`, `downloaded`, `rejected`)
  - submit/reject actions for approvers
  - computed download entitlements, enforced both in UI and in export methods
  - a reset-to-draft confirmation wizard that also resets the linked approval request

- Security becomes policy-driven:
  - base users see only their eligible letters
  - approval users/managers have controlled access to approvals letters and templates
  - HR users/managers can manage non-approval letters under explicit rules

---

## 2. Architectural Fit: How `letter_approval` Extends `letter_hr`

`letter_approval` builds on HR’s established extension points and adds governance.

Key integration points:

1. **Approval Request <-> Letter linkage**
   - `approval.request` gains `letter_ids`
   - `letter.letter` gains `approval_request_id`
   - a one-request-to-one-letter invariant is enforced at the database level

2. **Approval lifecycle -> Letter lifecycle**
   - request status changes map into letter status values via a dedicated sync method
   - submit/reject operations on letters forward into approvals actions and then reconcile the letter status

3. **Placeholder expansion using approvals metadata**
   - `*Addressed To*` is resolved from `approval.request.addressed_to` when approvals are enabled and a request exists

4. **Delivery method remains authoritative**
   - `delivery_method` introduced by `letter_hr` is still used to prevent downloads where policy requires physical handling
   - `letter_approval` further restricts download visibility and export execution based on delivery method, status, and role

---

## 3. File: `/models/letter_approval.py` (Extreme Detail)

---

## 3.1 Model Extension: `approval.category` (`class ApprovalCategory`)

```python
class ApprovalCategory(models.Model):
    _inherit = 'approval.category'
```

### 3.1.1 Purpose

This extension turns `approval.category` into an active policy source for “Letter Approval” by adding:

- a category-level policy field (`is_addressed_to`)
- reconciliation logic in `write()` that rebuilds approver lines on open requests whenever category approver configuration changes

This ensures that policy updates apply not only to future requests, but also to already-open requests.

---

### 3.1.2 Field: `is_addressed_to`

```python
CATEGORY_SELECTION = [
    ('required', 'Required'),
    ('optional', 'Optional'),
    ('no', 'None')]

is_addressed_to = fields.Selection(
    CATEGORY_SELECTION,
    string="Addressed To",
    default="required",
    required=True,
    help="Personnel/Organization this letter is addressed to (Only in a Letter Approval Request)"
)
```

Operational meaning:

- The field does not store the “Addressed To” content; it stores whether that content must exist on requests in this category.
- The request reads this value via a related field and uses it for UI requiredness and visibility.

---

### 3.1.3 Override: `write(self, vals)` (Policy-driven reconciliation)

```python
def write(self, vals):
    res = super().write(vals)

    if 'approver_ids' not in vals and 'manager_approval' not in vals:
        return res

    for category in self:
        if category.name != 'Letter Approval':
            continue

        requests = self.env['approval.request'].search([
            ('category_id', '=', category.id),
            ('request_status', 'in', ['new', 'pending']),
        ])

        for request in requests:
            old_request_status = request.request_status
            old_approver_ids = request.approver_ids

            status_map = {
                appr.user_id.id: appr.status
                for appr in old_approver_ids
                if appr.user_id
            }

            seen_user_ids = set()
            commands = [(5, 0, 0)]

            if category.manager_approval in ['approver', 'required']:
                employee = request.request_owner_id.employee_id
                manager = employee.parent_id if employee else False
                manager_user = manager.user_id if manager else False

                if manager_user and manager_user.id not in seen_user_ids:
                    if old_request_status == 'new':
                        manager_status = status_map.get(manager_user.id, 'new')
                    elif old_request_status == 'pending':
                        manager_status = status_map.get(manager_user.id, 'pending')
                    else:
                        manager_status = status_map.get(manager_user.id, 'new')

                    commands.append((0, 0, {
                        'user_id': manager_user.id,
                        'status': manager_status,
                        'required': category.manager_approval == 'required',
                    }))
                    seen_user_ids.add(manager_user.id)

            for cat_line in category.approver_ids:
                if not cat_line.user_id:
                    continue

                user = cat_line.user_id
                if user.id in seen_user_ids:
                    continue

                if old_request_status == 'new':
                    user_status = status_map.get(user.id, 'new')
                elif old_request_status == 'pending':
                    user_status = status_map.get(user.id, 'pending')
                else:
                    user_status = status_map.get(user.id, 'new')

                commands.append((0, 0, {
                    'user_id': user.id,
                    'status': user_status,
                    'required': cat_line.required,
                }))
                seen_user_ids.add(user.id)

            if commands:
                request.approver_ids = commands

            request.request_status = old_request_status

    return res
```

Behavioral breakdown:

1. **Executes normal write first**
   - Ensures category changes are persisted before dependent reconciliation.

2. **Early exit unless approver policy changed**
   - Limits reconciliation to changes in `approver_ids` or `manager_approval` only.

3. **Hard scopes to one category by name**
   - Only the “Letter Approval” category triggers request reconciliation.

4. **Targets only open requests**
   - Only `new` and `pending` requests are impacted; closed requests are untouched.

5. **Preserves request status**
   - Saves `old_request_status` because rebuilding approver lines can trigger framework logic that recalculates request state.

6. **Preserves per-user approver status where possible**
   - Builds `status_map` so existing approvers keep their status (when still present after policy changes).

7. **Rebuilds approver lines deterministically**
   - `(5, 0, 0)` clears the One2many.
   - Manager approver is added first if configured.
   - Category approver lines are added next.
   - `seen_user_ids` prevents duplicates (common case: manager is also explicitly listed as a category approver).

8. **Assigns approver statuses according to request phase**
   - For `new` requests, default approver status is `new`.
   - For `pending` requests, default approver status is `pending`.
   - If the user existed previously, the old status is reused.

9. **Applies rebuilt lines and restores request status**
   - `request.approver_ids = commands` recreates the approver lines.
   - `request.request_status = old_request_status` prevents unintended lifecycle movement.

Operational implications:

- Changing approver policy is not “future-only”; it rewrites approvers on active requests.
- This is correct for policy enforcement, but requires controlled administrative change management.

---

## 3.2 Model Extension: `approval.request` (`class ApprovalRequest`)

```python
class ApprovalRequest(models.Model):
    _inherit = 'approval.request'
```

### 3.2.1 Purpose

This extension converts standard approvals requests into “Letter Approval Requests” by adding:

- request metadata used for letters and placeholders
- linkage to letters
- role flags for view governance
- template selection logic with different UX and access for request owners vs approvers/managers
- enforcement rules preventing final approval without a linked letter for the Letter Approval category

---

### 3.2.2 Fields

#### Category policy reflection

```python
is_addressed_to = fields.Selection(related="category_id.is_addressed_to")
```

- Mirrors the category-level policy to the request for view logic.

#### Request metadata

```python
addressed_to = fields.Text(string="Addressed To")
special_content = fields.Text(string="Special Content")
```

- `addressed_to` is used to replace `*Addressed To*` in letters.
- `special_content` is stored as supplemental request text (currently used in UI, and available for future placeholder expansion).

#### Linkage to letters

```python
letter_ids = fields.One2many('letter.letter', 'approval_request_id')
```

- Links all letters tied to this request (one-to-one expected by business logic).

#### Digital-only projection for base users

```python
digital_letter_ids = fields.One2many(
    'letter.letter',
    compute='_compute_digital_letters',
    string="Digital Letters",
    store=False,
)
```

- Used in request views to show only downloadable letters to normal users.

#### Role and category flags

```python
is_request_owner = fields.Boolean(compute='_compute_is_request_owner', store=False)
is_approver_manager = fields.Boolean(compute='_compute_is_approver_manager', store=False)
is_letter_approval = fields.Boolean(compute='_compute_is_letter_approval', store=False)
```

- `is_letter_approval` gates letter-specific UI logic.
- `is_request_owner` is used to split “Linked Letters” visibility between base users and approval roles.
- `is_approver_manager` is used to hide request-owner-only fields when the viewer is an approver/manager.

---

### 3.2.3 Template selection infrastructure

#### Real template reference

```python
template_id = fields.Many2one('letter.template', string='Letter Type')
```

- The canonical relation.

#### Restricted selection proxy for request owners

```python
template_select = fields.Selection(
    selection='_get_template_selection',
    string="Letter Type",
    required=True,
    compute='_compute_template_select',
    inverse='_inverse_template_select',
    groups="base.group_user"
)
```

- Enables a controlled selection UI for base users.

Key intent:

- Request owners select a template through `template_select`.
- Approvers/managers select the template through `template_id` directly (separate field, separate group visibility in views).

---

### 3.2.4 Methods (Extreme, Method-by-Method Detail) — `approval.request`

This section documents the methods introduced on the `approval.request` extension in `/models/letter_approval.py`, with a focus on runtime behavior, dependencies, and how each method supports the approvals-governed letter workflow.

---

#### `_compute_is_letter_approval(self)`

```python
@api.depends('category_id')
def _compute_is_letter_approval(self):
    for record in self:
        record.is_letter_approval = record.category_id.name and record.category_id.name == "Letter Approval"
```

**Purpose**

Determines whether the current approval request belongs to the “Letter Approval” category and stores the result in `is_letter_approval` (non-stored).

**Runtime Behavior**

- Triggered whenever `category_id` changes.
- For each request record:
  - Reads `record.category_id.name`.
  - Sets:
    - `True` when the category exists and its name equals `"Letter Approval"`.
    - `False` otherwise.

**Why it exists**

The module injects several UI elements and behavioral rules that should apply only to letter approval requests, not to other approval categories in the system. This compute flag becomes a single “gate condition” used in XML (`invisible`, `required`) to prevent unrelated approval workflows from inheriting letter-only behavior.

**Important considerations**

- Category name comparison is string-based.
  - If the category is renamed in production, the workflow gating breaks.
  - A more robust approach would be to gate using an XML ID or a dedicated boolean on the category (but this implementation is valid as long as naming is controlled).

---

#### `_compute_digital_letters(self)`

```python
@api.depends('letter_ids')
def _compute_digital_letters(self):
    for record in self:
        record.digital_letter_ids = record.letter_ids.filtered(lambda l: l.delivery_method == 'digital')
```

**Purpose**

Builds a computed subset (`digital_letter_ids`) of linked letters that are downloadable because they were created with `delivery_method = 'digital'`.

**Runtime Behavior**

- Recomputes whenever the One2many `letter_ids` changes.
- For each request record:
  - Filters `record.letter_ids` to only letters whose `delivery_method == 'digital'`.
  - Assigns the resulting recordset to `digital_letter_ids`.

**Why it exists**

The UI intentionally provides different “linked letters” views depending on role:

- Request owners (base users) should only see letters that are valid to download (digital delivery).
- Approvers/managers can see all linked letters.

Rather than embedding that logic repeatedly in view domains, the module centralizes it into a computed One2many that the view can use directly.

**Important considerations**

- `digital_letter_ids` is `store=False` so it is computed at runtime and not persisted.
- Filtering requires reading each linked letter’s `delivery_method`. If access rules block that field for base users, this compute would return an empty set. In the current access model, base users have read access to letters, but record rules restrict which letters appear. That’s intentional.

---

#### `_compute_is_request_owner(self)`

```python
def _compute_is_request_owner(self):
    for record in self:
        if record.request_owner_id:
            record.is_request_owner = record.request_owner_id.id == record.env.user.id
```

**Purpose**

Computes whether the current viewer is the request owner.

**Runtime Behavior**

- For each request record:
  - If `request_owner_id` exists:
    - Compares `request_owner_id.id` to `env.user.id`.
    - Sets `is_request_owner` accordingly.

  - If `request_owner_id` is missing:
    - The method does not explicitly assign `False`, leaving the default value (False). This is acceptable but relies on default initialization.

**Why it exists**

The approvals request form is shared among roles. The module uses this flag to:

- Hide approver-only panels from request owners.
- Show request-owner-only inputs (like `template_select`) only when the viewer is the owner.
- Control which linked letters list is displayed (full list for approvers/managers vs digital-only list for owners).

**Important considerations**

- No `@api.depends` decorator is present in the provided code for this method. In Odoo, a compute field should generally have depends so the framework knows when to recompute.
  - In practice, because the value is viewer-dependent (`env.user`), it is not purely deterministic from record fields anyway.
  - The implementation treats it as runtime/UI gating, not as a business field. That is consistent with `store=False`.

---

#### `_compute_is_approver_manager(self)`

```python
def _compute_is_approver_manager(self):
    for record in self:
        record.is_approver_manager = (
            record.env.user.has_group('approvals.group_approval_user')
            or record.env.user.has_group('approvals.group_approval_manager')
        )
```

**Purpose**

Computes whether the current viewer is in an approvals role (user or manager).

**Runtime Behavior**

- For each record:
  - Checks group membership of the current user:
    - `approvals.group_approval_user`
    - `approvals.group_approval_manager`

  - Sets the boolean accordingly.

**Why it exists**

This flag acts as a UI and logic switch for separating request owner operations from approval authority operations. Example: request owners use `template_select` (selection proxy), while approvers/managers can access the direct `template_id` Many2one field.

**Important considerations**

- As with `is_request_owner`, this is viewer-dependent and `store=False`.
- The check uses `or`, so managers implicitly qualify as approver-managers even if only manager group is assigned.

---

#### `_compute_template_select(self)`

```python
@api.depends('template_id')
def _compute_template_select(self):
    for record in self:
        record.template_select = record.template_id.id if record.template_id else False
```

**Purpose**

Keeps the proxy selection field `template_select` consistent with the canonical Many2one `template_id`.

**Runtime Behavior**

- Triggered when `template_id` changes.
- For each record:
  - If `template_id` exists:
    - assigns `template_select = template_id.id`

  - Else:
    - assigns `template_select = False`

**Why it exists**

The module supports two template-selection experiences:

- Request owners select a template through `template_select` (Selection field).
- Approvers/managers select a template through `template_id` (Many2one).

This compute ensures that if a privileged user sets `template_id`, the request owner view (which renders `template_select`) will display the corresponding selection.

**Important considerations**

- `template_select` stores IDs (as selection values), which are typically strings in the client payload.
- The compute writes integers into `template_select`. Odoo generally tolerates this because selection values can be strings or ints depending on implementation, but consistency is important. The inverse handles conversion back to int for Many2one.

---

#### `_inverse_template_select(self)`

```python
def _inverse_template_select(self):
    for record in self:
        sel = record.template_select

        if not sel:
            record.template_id = False
            continue

        if isinstance(sel, (list, tuple)):
            sel = sel[0]

        try:
            sel_id = int(sel)
        except (TypeError, ValueError):
            sel_id = False

        record.template_id = sel_id
```

**Purpose**

Applies request-owner template selection into the canonical `template_id` field.

**Runtime Behavior (Step-by-step)**

For each request:

1. Read `sel = record.template_select`.

2. **Empty selection**
   - If `sel` is falsy:
     - Clears `template_id` and continues to next record.

3. **Client payload normalization**
   - If `sel` is a list or tuple:
     - Takes the first element (`sel[0]`).

   - This guards against malformed RPC payloads where the client sends `[id, display_name]` or similar.

4. **Type normalization**
   - Attempts `int(sel)`.
   - If conversion fails:
     - Sets `sel_id = False`.

5. **Write to Many2one**
   - Assigns `record.template_id = sel_id`.

**Why it exists**

Selection fields are often used to avoid exposing direct Many2one relations to certain users. Here, `template_select` is available to base users (request owners), while `template_id` is intended for approvals roles.

This inverse enables base users to drive the Many2one indirectly in a controlled way.

**Important considerations**

- This method is intentionally defensive to deal with unpredictable client payload types.
- If `sel_id` becomes `False`, `template_id` is cleared. This prevents invalid IDs from corrupting the request.

---

#### `_get_template_selection(self)`

```python
@api.model
def _get_template_selection(self):
    try:
        templates = self.env['letter.template'].sudo().search([])
        return [(t.id, t.template_name) for t in templates] or []
    except Exception as e:
        return []
```

**Purpose**

Provides the selection options for `template_select` by reading all `letter.template` records.

**Runtime Behavior**

- Executes in model context (`@api.model`).
- Uses `sudo()` to fetch templates regardless of the current user’s access to templates.
- Returns a list of tuples: `(template.id, template.template_name)`.

**Why it exists**

Base users are denied access to templates by design (`ir.model.access.csv` sets base users to `0,0,0,0` for templates). However, request owners still need to choose a template type when creating a letter approval request. `sudo()` makes that possible without granting template access rights.

**Important considerations**

- This is a deliberate security tradeoff:
  - Base users cannot open templates, but they can see template names in a selection dropdown.

- The method catches all exceptions and returns an empty list:
  - This prevents hard failures in the approvals form if template retrieval fails.
  - The downside is silent failure; template selection would become unusable without a clear error message.

---

#### `create(self, vals)` (multi-create aware)

```python
@api.model
def create(self, vals):
    if isinstance(vals, list):
        for val in vals:
            if val.get('template_select') and not val.get('template_id'):
                val['template_id'] = val['template_select']
        return super().create(vals)

    if vals.get('template_select') and not vals.get('template_id'):
        vals['template_id'] = vals['template_select']
    return super().create(vals)
```

**Purpose**

Ensures `template_id` is properly populated from `template_select` at create-time.

**Runtime Behavior**

- Handles both Odoo create styles:
  - `vals` is a dict (single record)
  - `vals` is a list of dicts (batch create)

Processing logic:

- For each record payload:
  - If `template_select` exists and `template_id` is missing:
    - Copies `template_select` into `template_id`

**Why it exists**

`template_select` is a computed/inverse field and may not always invoke inverse logic before create, depending on how the client submits values. This method makes create robust by enforcing canonical `template_id` population.

**Important considerations**

- This write occurs before `super().create(...)`, so downstream logic sees a consistent `template_id`.
- It assumes selection values are valid template IDs. If the selection returns strings, Odoo often casts Many2one values appropriately; if not, an integer cast could be added for consistency.

---

### 3.2.5 `_sync_letter_status(self)` (Request → Letter status mapping)

```python
def _sync_letter_status(self):
    for request in self:
        if not request.letter_ids:
            continue
        if request.request_status == 'approved':
            request.letter_ids.status = 'issued'
        elif request.request_status == 'refused':
            request.letter_ids.status = 'rejected'
        elif request.request_status == 'cancel':
            request.letter_ids.status = 'draft'
        elif request.request_status == 'new':
            request.letter_ids.status = 'draft'
        elif request.request_status == 'pending':
            request.letter_ids.status = 'pending'
```

**Purpose**

Keeps linked letters aligned with the approval request’s lifecycle.

**Runtime Behavior**

- For each request:
  - If no linked letters: no action.
  - If letters exist:
    - Sets `letter.status` based on request status.

Mapping table:

| `approval.request.request_status` | `letter.letter.status` |
| --------------------------------- | ---------------------- |
| `new`                             | `draft`                |
| `pending`                         | `pending`              |
| `approved`                        | `issued`               |
| `refused`                         | `rejected`             |
| `cancel`                          | `draft`                |

**Why it exists**

There are multiple places where request status can change:

- approvers approving/refusing
- withdrawals
- admin actions

Without a central sync method, letter status could drift from the request status and create incorrect UI states (e.g., downloadable letter while request is still pending).

**Important considerations**

- This method assigns `status` on a recordset (`request.letter_ids.status = ...`), which is efficient and correct.
- It assumes one letter per request. If that invariant is ever relaxed, all linked letters will be synchronized together.

---

### 3.2.6 Override: `action_approve(self, approver=None)`

```python
def action_approve(self, approver=None):
    self.ensure_one()

    if self.category_id.name == 'Letter Approval':
        if self.approver_sequence:
            waiting = self.approver_ids.filtered(lambda a: a.status == 'waiting')
            if not waiting and not self.letter_ids:
                raise UserError(...)

        else:
            approved_count = len(self.approver_ids.filtered(lambda a: a.status == 'approved'))
            pending_approvers = self.approver_ids.filtered(lambda a: a.status == 'pending')
            if pending_approvers and (approved_count+1) >= self.approval_minimum and not self.letter_ids:
                raise UserError(...)

    res = super().action_approve(approver=approver)

    if self.category_id.name == 'Letter Approval':
        self._sync_letter_status()

    return res
```

**Purpose**

Adds a “letter must exist before final approval” rule for Letter Approval requests, then keeps letter state synchronized after approval.

**Runtime Behavior (Step-by-step)**

1. `ensure_one()`
   - Enforces single-request execution (approval actions are typically executed one at a time).

2. Category gate: only applies if category name is “Letter Approval”
   - Other approval categories behave normally.

3. Sequenced approvals branch (`self.approver_sequence == True`)
   - Finds approvers still in `waiting`.
   - If **no waiting approvers remain**, current approver is effectively last in sequence.
   - If last in sequence and **no linked letter exists**, raises `UserError` and blocks approval.

4. Parallel approvals branch (`self.approver_sequence == False`)
   - Computes:
     - how many approvers are already approved
     - whether there are pending approvers

   - Checks whether approving now would satisfy `approval_minimum`:
     - `(approved_count + 1) >= self.approval_minimum`

   - If yes and no letter exists, blocks approval.

5. Calls parent `action_approve`
   - Performs actual approval state updates inside approvals module.

6. Post-approval sync (only for Letter Approval)
   - Calls `_sync_letter_status()` to align the linked letter status with the updated request status.

**Why it exists**

The system design requires a letter to be drafted/attached before the request can be fully approved. This guarantees:

- approvers are approving an actual letter artifact, not an empty request
- request owners cannot receive an “approved” request without a generated letter

**Important considerations**

- The logic relies on category name equality.
- The enforcement is tied to “final approval condition” rather than “any approval”:
  - Approvers early in the chain can approve without a letter.
  - The last approval threshold requires letter existence.

---

### 3.2.7 `action_refuse` and `action_withdraw`

#### `action_refuse(self, approver=None)`

```python
def action_refuse(self, approver=None):
    self.ensure_one()
    super().action_refuse()
    self._sync_letter_status()
```

**Purpose**

Refuses a request and ensures the linked letter is marked rejected.

**Runtime Behavior**

- Enforces singleton.
- Calls parent refusal logic.
- Immediately syncs linked letters.

**Important considerations**

- Sync is unconditional (not gated by category name in the current code). If the module is only intended to sync for Letter Approval category, gating could be added, but unconditional syncing is safe if linked letters exist only in this workflow.

---

#### `action_withdraw(self, approver=None)`

```python
def action_withdraw(self, approver=None):
    self.ensure_one()
    super().action_withdraw()
    if self.letter_ids:
        approvers = self.approver_ids.filtered(lambda a: a.status == 'pending')
        if len(approvers) == self.approval_minimum:
            self.letter_ids.status = 'draft'
        else:
            self.letter_ids.status = 'pending'
        self._sync_letter_status()
```

**Purpose**

Handles the withdrawal case and keeps letter status aligned.

**Runtime Behavior**

- Singleton enforcement.
- Calls parent withdraw logic.
- If a linked letter exists:
  - checks number of approvers in `pending`
  - assigns an intermediate letter status (`draft` or `pending`)
  - then calls `_sync_letter_status()` which may override or confirm the final status depending on request state.

**Why it exists**

Withdrawals can put an approval request into a state that requires careful UX handling. The letter should not remain issued/downloadable if approvals are withdrawn.

**Important considerations**

- There is overlapping logic:
  - manual `letter_ids.status = ...`
  - then `_sync_letter_status()`

- If request status after withdrawal is deterministic, the pre-sync assignment could be redundant. If the intention is to influence how the request status is interpreted, it should be explicit. As implemented, `_sync_letter_status()` is the authoritative final alignment step.

---

### 3.2.8 `action_create_letter(self)`

```python
def action_create_letter(self):
    self.ensure_one()

    letter = self.env['letter.letter'].create({
        'letter_name': self.name or "Approval Letter",
        'has_approval_request': True,
        'approval_request_id': self.id,
        'request_owner_name': self.request_owner_id.name if self.request_owner_id else '',
        'template_id': self.template_id.id if self.template_id else None,
        'employee_id': self.request_owner_id.employee_id.id if self.template_id and self.request_owner_id.employee_id else None,
        'delivery_method' : 'digital',
    })

    return {
        'name': 'Letter',
        'type': 'ir.actions.act_window',
        'res_model': 'letter.letter',
        'view_mode': 'form',
        'res_id': letter.id,
        'target': 'current',
    }
```

**Purpose**

Creates a letter record from an approval request without approving the request automatically, and redirects the user to the created letter.

**Runtime Behavior (Step-by-step)**

1. Singleton enforcement (`ensure_one()`)

2. Creates `letter.letter` with a prefilled payload:

- `letter_name`:
  - uses request name if present, else fallback `"Approval Letter"`.
  - Note: in the core letter system, `letter_name` may be computed; this value is still useful as a label or as initial content depending on implementation.

- `has_approval_request = True`:
  - activates approvals-governed behavior on the letter side.

- `approval_request_id = self.id`:
  - creates the link required by record rules, views, and status sync.

- `request_owner_name`:
  - stored/related depending on letter model; included for UI.

- `template_id`:
  - copied from request `template_id`.

- `employee_id`:
  - inferred from request owner’s employee when available.
  - This integrates with `letter_hr` placeholder logic and HR fields.

- `delivery_method = 'digital'`:
  - default aligned with the “download through approvals” workflow.
  - If physical delivery is desired in this approvals flow, the letter can still be changed while in draft, but the default optimizes for the common approvals outcome.

3. Returns an action opening the new letter in form view.

**Why it exists**

The approval request is the workflow container, but the letter is the artifact that must be reviewed, approved, issued, then downloaded. Creating the letter as a separate action supports the “letter must exist before final approval” rule enforced in `action_approve`.

**Important considerations**

- The method does not call `_sync_letter_status()`. At this stage the request is still pending/new, and the created letter is expected to start in `draft` until submission/approvals progress.
- The one-request-to-one-letter constraint ensures this action cannot be used repeatedly for the same request.

---

## 3.3 Model Extension: `letter.letter` (`class LetterLetter`)

```python
class LetterLetter(models.Model):
    _inherit = 'letter.letter'
```

### 3.3.1 Purpose

This extension introduces an approval-aware workflow while keeping the system usable for HR letters that do not require an approval request.

The controlling field is:

- `has_approval_request`: when enabled, all logic routes through approvals governance; when disabled, the letter behaves like a direct-issuance HR letter with controlled download rules.

---

### 3.3.2 Fields

#### `status`

- Adds a lifecycle state machine compatible with approval requests.

#### `approval_request_id`

- Links the letter to the approval request.
- Label “Request Reason” is used to match business wording.

#### `request_owner_name`

- Related display field.

#### `can_submit`

- Governs visibility of Submit/Reject actions.

#### `can_download`

- Governs visibility of export actions and download buttons in list views.

#### `template_id` (temporary relaxation)

- Requiredness removed in this branch to allow template inference from approval request in onchange logic.

#### `addressed_to`

- Related field used for placeholder replacement.

#### `has_approval_request`

- Master switch controlling governance mode.

#### One-request-to-one-letter invariant

```python
_unique_approval_request_letter = models.Constraint(
     "UNIQUE(approval_request_id)",
     "Each approval request can only be linked to one letter.")
```

- Prevents multiple letters from linking to the same request.
- This enforces the design invariant used throughout the UI domains and actions.

---

### 3.3.3 Onchange methods

#### `_clear_request`

- When approvals mode is turned off, clears `approval_request_id` to avoid mixed-mode state.

#### `_compute_employee` (mode + request aware)

- When approvals mode is on and a request is selected:
  - sets `template_id` from the request
  - if template module is HR:
    - sets employee to request owner’s employee

  - else:
    - clears employee to prevent invalid HR placeholder resolution

---

### 3.3.4 `_compute_replaced_content` (adds `*Addressed To*`)

Execution order:

1. `ensure_one()` enforces singleton execution.
2. `super()._compute_replaced_content()` runs base/HR placeholder resolution first.
3. If approvals mode is enabled and request exists:
   - reads template under `sudo()` to avoid access issues
   - only applies replacement when template module is HR
   - replaces `*Addressed To*` with request’s `addressed_to` (sudo read), fallback `N/A`

This method intentionally does not attempt to apply approvals placeholders to non-HR templates.

---

### 3.3.5 Workflow actions

#### `submit_action`

- Approvals mode:
  - requires a request
  - ensures the user is eligible to approve now (not “waiting” behind another approver in sequence)
  - approves user’s approver line
  - sets letter status:
    - `pending` during approvals
    - `issued` when request becomes approved
    - `rejected` when request becomes refused

- Non-approvals mode:
  - directly sets `status = 'issued'`

#### `reject_action`

- Approvals mode:
  - requires a request
  - if user is a pending approver, triggers request refusal
  - sets letter status based on request status

- Non-approvals mode:
  - directly sets `status = 'rejected'`

---

### 3.3.6 `_compute_can_submit` (role + status aware)

- Approvals mode:
  - current user can submit if they are an approver on the request and not fully approved yet

- Non-approvals mode:
  - always true (submission is direct issuance)

---

### 3.3.7 `_compute_can_download` (download entitlement matrix)

This method centralizes download entitlement for:

- request owners
- approvers/managers
- HR users/managers
- employees (for non-approval letters)

Inputs:

- `has_approval_request`
- `approval_request_id`
- `status`
- `delivery_method`
- group membership (`approvals`, `hr`)
- ownership relations (request owner, employee user)

Outputs:

- `can_download` True/False

High-level behavior:

- Approvals mode:
  - request owners: can download only digital letters when status is `issued` or `downloaded`
  - non-owners (approvers/managers): can download when status is `issued`, `downloaded`, or `rejected`

- Non-approvals mode:
  - employee: can download only digital letters when status is `issued` or `downloaded`
  - HR users/managers: can download when status is `issued`, `rejected`, or `downloaded` (subject to record rules)

This computed result is used by views and export method guards to prevent bypass.

---

### 3.3.8 Export enforcement: `print_letter()` and `save_letter_docx()`

Both export entry points enforce:

- Draft letters cannot be downloaded.
- When the rightful downloader triggers an issued letter download, status is upgraded to `downloaded` using `sudo().write(...)`.
- Exports only proceed when policy allows and status is eligible.

The write is performed via `sudo()` because base users are intentionally blocked from writing letters by access rules; the system still needs to record the “downloaded” state as an audit-like transition.

---

## 3.4 Wizard: `letter.reset.wizard`

```python
class LetterResetWizard(models.TransientModel):
    _name = 'letter.reset.wizard'
```

Purpose:

- Adds a confirmation step before resetting a letter from pending/issued state back to draft.
- Resets the linked approval request back to draft.
- Clears `approval_request_id` to avoid mixed state.

`action_confirm_reset` uses `sudo()` to ensure it can safely reset approval workflow state even when the user lacks write access to approval records.

---

## 4. Security Model (Files in `/security/`)

This module’s behavior relies heavily on access rights, implied groups, and record rules. The security layer is not auxiliary; it is part of the business logic enforcement.

---

### 4.1 File: `/security/group_security.xml` (Group implied access)

```xml
<record id="approvals.group_approval_user" model="res.groups">
    <field name="implied_ids" eval="[(4, ref('letter_writer.group_letter_writer_access'))]"/>
</record>

<record id="approvals.group_approval_manager" model="res.groups">
    <field name="implied_ids" eval="[(4, ref('approvals.group_approval_user')),
                                     (4, ref('letter_writer.group_letter_writer_access'))]"/>
</record>
```

Behavioral intent:

- Any user placed into `approvals.group_approval_user` automatically gains `letter_writer.group_letter_writer_access`.
- Any approvals manager automatically gains:
  - approvals user privileges
  - letter writer access

This avoids manual dual-assignment of groups and ensures approvers can access letter screens and objects that are governed by letter writer’s base group.

Operational impact:

- Approvals roles become the primary gateway to templates and letters in approval-governed workflows.
- This is especially important because `letter_approval` disables some default model access entries and redefines access through approvals groups.

---

### 4.2 File: `/security/override_security.xml` (Disable permissive core access)

The module disables the default `ir.model.access` entries shipped by `letter_writer` for templates and letters.

Intent:

- The original core access is permissive enough to bypass approval governance.
- By disabling those entries, the effective access becomes the matrix defined in `letter_approval`’s own `ir.model.access.csv`.

---

### 4.3 File: `/security/ir.model.access.csv` (Access matrix)

```csv
access_letter_template_user,...,letter_writer.model_letter_template,base.group_user,0,0,0,0
access_letter_template_approver,...,letter_writer.model_letter_template,approvals.group_approval_user,1,1,1,1
access_letter_template_manager,...,letter_writer.model_letter_template,approvals.group_approval_manager,1,1,1,1

access_letter_letter_user,...,letter_writer.model_letter_letter,base.group_user,1,0,0,0
access_letter_letter_approver,...,letter_writer.model_letter_letter,approvals.group_approval_user,1,1,1,1
access_letter_letter_manager,...,letter_writer.model_letter_letter,approvals.group_approval_manager,1,1,1,1

access_letter_reset_wizard_user,...,letter_approval.model_letter_reset_wizard,base.group_user,0,0,0,0
access_letter_reset_wizard_approver,...,letter_approval.model_letter_reset_wizard,approvals.group_approval_user,1,1,1,1
access_letter_reset_wizard_manager,...,letter_approval.model_letter_reset_wizard,approvals.group_approval_manager,1,1,1,1
```

Key outcomes:

- **Base users**
  - cannot access templates at all
  - can read letters (but cannot create/write/unlink)
  - cannot use reset wizard

- **Approvals users/managers**
  - can manage templates and letters fully
  - can use the reset wizard

This ensures:

- request owners do not author templates
- request owners do not manipulate approval-governed letters directly (except through controlled actions that may internally use `sudo()` to record transitions)

---

### 4.4 File: `/security/record_rules_security.xml` (Row-level enforcement)

This file defines who can see and operate on which letters based on ownership and workflow mode.

#### 4.4.1 Approval-governed letters for base users

```xml
domain_force="[('approval_request_id.request_owner_id','=',user.id),
              ('status','in',['issued','downloaded'])]"
```

- Base users can read only their own letters (by request ownership) and only when issued/downloaded.
- Draft and pending letters are not visible to request owners via direct letter menus, which prevents early access before issuance.

#### 4.4.2 Approval-governed letters for approvers (created by me vs not created by me)

Two rules split the world into:

- Letters the approver created (full CRUD)
- Letters the approver did not create (write allowed, but create/unlink blocked)

Both require that the user is an approver on the linked request:

```xml
('approval_request_id.approver_ids.user_id','=',user.id)
```

This expresses the business statement: only approvers assigned to the request can operate on the letters linked to that request.

#### 4.4.3 Approval-governed letters for approval managers

```xml
domain_force="[
  ('approval_request_id.request_owner_id','!=',user.id),
  ('approval_request_id.approver_ids.user_id','=',user.id)
]"
```

- Managers can manage letters when they are involved as approvers and they are not the request owner.
- This prevents a request owner who is also a manager from trivially bypassing owner restrictions.

#### 4.4.4 Non-approval letters: HR users and managers

```xml
domain_force="[
  ('has_approval_request', '=', False),
  '|',
    ('employee_id.user_id', '!=', user.id),
    ('status', '!=', 'draft')
]"
```

Interpretation:

- Applies only when the letter is not approval-governed.
- HR users/managers can manage:
  - other employees’ non-approval letters freely
  - their own non-approval letters only when not draft (prevents self-issuing draft artifacts without governance)

#### 4.4.5 Non-approval letters: employees (base users)

```xml
domain_force="[
  ('has_approval_request', '=', False),
  ('employee_id.user_id', '=', user.id),
  ('status','in',('issued','downloaded'))
]"
```

- Employees can read only their own non-approval letters and only when issued/downloaded.

#### 4.4.6 Non-approval letters: managers viewing direct reports

```xml
domain_force="[('employee_id.parent_id.user_id','=',user.id)]"
```

- Allows a manager to read letters of employees who report to them (based on HR hierarchy).

---

## 5. Views (Files in `/views/`)

---

### 5.1 File: `/views/approval_views.xml`

#### 5.1.1 Approval Category form extension

Adds `is_addressed_to` as a radio widget, but only visible when the category name is “Letter Approval”.

```xml
<field name="is_addressed_to" widget="radio"
       options="{'horizontal': true}"
       invisible="name != 'Letter Approval'"/>
```

This ensures the field is not displayed for unrelated categories, keeping the approvals UI clean.

---

#### 5.1.2 Approval Request form extension (request details)

Inside the request details group:

1. `addressed_to`
   - hidden when the request is not Letter Approval or when policy is “no”
   - required only when policy is “required”

```xml
<field name="addressed_to"
       invisible="not is_letter_approval or is_addressed_to == 'no'"
       required="is_letter_approval and is_addressed_to == 'required'"/>
```

2. Template selection split by role:

- Request owner (base user) uses `template_select`, hidden when:
  - not the request owner
  - user is approver/manager
  - not letter approval

```xml
<field name="template_select"
       invisible="request_owner_id != uid or is_approver_manager or not is_letter_approval"
       required="True"
       groups="base.group_user"/>
```

- Approvers/managers use `template_id`, visible only for letter approval and only to approvals groups:

```xml
<field name="template_id"
       required="True"
       invisible="not is_letter_approval"
       groups="approvals.group_approval_user,approvals.group_approval_manager"/>
```

This split aligns with the access model: base users cannot read templates directly, but can still choose one through a controlled selection.

---

#### 5.1.3 Approval Request form: special pages and linked letters

Adds:

- “Special Content” page (letter approval only)
- “Linked Letters” page (letter approval only)

Two different list widgets exist:

- For approvals roles:
  - shows `letter_ids`
  - hidden for request owners

- For base users:
  - shows `digital_letter_ids`
  - visible only for request owners

Each list includes a “Download (PDF)” button calling `print_letter` and controlled by `invisible="not can_download"`.

This implements “users only see what they can download” at the request level as well.

---

#### 5.1.4 “Create Letter” button injection

Adds `action_create_letter` next to `action_approve`, with visibility rules based on:

- attachment count
- request status
- current user status (`user_status == 'pending'`)
- category relevance (`is_letter_approval`)

This supports a governed step where approvers can create the letter before final approval conditions are met.

---

### 5.2 File: `/views/confirmation_wizard_view.xml`

Defines the reset confirmation dialog:

- explains that resetting the letter also resets the linked request
- provides explicit Yes/No actions

This prevents silent destructive workflow resets.

---

### 5.3 File: `/views/letter_views.xml`

#### 5.3.1 Letter list view extension

Adds approval context columns and a badge-based `status` visualization.

Also forces export buttons to be hidden when `not can_download`.

---

#### 5.3.2 Letter form view extension (workflow controls)

Key governance points:

- Core fields become readonly when `status != 'draft'`:
  - `letter_date`, `company_id`, `employee_id`, `template_id`, `delivery_method`, `employee_contract_id`

- Injects workflow buttons before export:
  - Submit: visible while not final and when `can_submit`
  - Reject: draft-only and when `can_submit`
  - Reset to Draft: visible in pending only

- Export buttons are governed by `not can_download`

- Adds statusbar in header with visible states:
  - draft → pending → issued → downloaded → rejected

- Adds approvals toggle + request field inside optional-fields slot:

```xml
<field name="has_approval_request" widget="boolean_toggle"/>
<field name="approval_request_id"
       required="has_approval_request"
       domain="[('approver_ids.user_id','=',uid),
               ('request_status','=','pending'),
               ('letter_ids','=',False),
               ('category_id.name','=','Letter Approval')]"
       invisible="not has_approval_request"/>
```

This domain enforces that the selected request must be:

- assigned to the current user as an approver
- pending
- not already linked to a letter
- in the correct category

This prevents attaching arbitrary approval requests and preserves the one-request-to-one-letter invariant.

---

#### 5.3.3 Search view extension

Adds filters:

- Assigned to Me (approver-based)
- My Letters (employee ownership based)
- Created by Me

These align with record rules and reduce user confusion when letters are filtered by governance logic.

---

#### 5.3.4 Menu governance for templates

Disables the base Templates menu and reintroduces Templates menu restricted to approvals groups only.

This matches the access model: base users do not manage templates.

---

## 6. Summary: Governance Pattern Added by `letter_approval`

`letter_approval` demonstrates the next scalability tier of the Letter Writer architecture:

- Business workflow is implemented by layering:
  - approval request metadata
  - letter lifecycle state machine
  - download entitlements
  - UI enforcement
  - security enforcement

- The module remains modular because it does not rewrite core letter generation or output logic; it governs when and by whom those capabilities can be executed.
