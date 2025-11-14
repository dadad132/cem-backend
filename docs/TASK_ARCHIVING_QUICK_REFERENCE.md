# Task Archiving - Quick Reference

## What It Does
When you mark a task as "Done", it automatically archives and disappears from the kanban board. The task remains in the system with full history, but only appears in the list view's "Done" tab.

## For Regular Users

### Completing Tasks
1. Move task to "Done" column on kanban board, OR
2. Edit task and change status to "Done"
3. Task automatically archives and leaves the board

### Viewing Archived Tasks
1. Go to **Tasks → List View**
2. Click the **"Done"** tab
3. Archived tasks show a yellow "🗄️ ARCHIVED" badge

### Accessing Archived Task Details
- Click any archived task from the Done tab
- You can **view** all information:
  - Original task details
  - All comments
  - Attachments
  - Edit history
- You **cannot** edit or comment (read-only)

### What You'll See
```
⚠️ This task is archived and read-only. Only admins can modify or reopen it.
```

## For Admin Users

### Everything Regular Users Can Do, Plus:

### Editing Archived Tasks
1. Open archived task from Done tab
2. Edit form is **enabled** (not hidden)
3. Make changes and save normally

### Adding Comments to Archived Tasks
1. Open archived task from Done tab
2. Comment form is **enabled**
3. Add comments with attachments as usual

### Reopening Archived Tasks
1. Open archived task from Done tab
2. Look for blue info box at top:
   ```
   This task is archived. You can reopen it to move it back to the board.
   [🔓 Reopen Task]
   ```
3. Click **"Reopen Task"** button
4. Task returns to board with status changed to "In Progress"

## Workflow Examples

### Example 1: Bug Fix Complete
**Scenario**: Developer completes bug fix

1. User moves task to "Done" column
2. Task archives automatically
3. Week later, bug reappears
4. **Admin**: Opens task from Done tab → Clicks "Reopen Task"
5. Task returns to board with "In Progress" status
6. Developer can continue working

### Example 2: Audit Review
**Scenario**: Manager needs to review completed work

1. Manager goes to Tasks → List View → Done tab
2. Sees all completed tasks with ARCHIVED badge
3. Clicks task to view full details
4. Reviews comments, attachments, time logs
5. Cannot accidentally modify completed work

### Example 3: Incorrect Completion
**Scenario**: Task marked done by mistake

1. User realizes task not actually complete
2. **Non-Admin**: Cannot reopen (contacts admin)
3. **Admin**: Opens task → Clicks "Reopen Task"
4. Task back on board for user to complete properly

## Visual Indicators

### In List View
```
Task Title  🗄️ ARCHIVED  [Status: Done] [Priority: High]
```

### In Task Detail (Non-Admin)
```
┌─────────────────────────────────────────────┐
│ Task Title                    🗄️ ARCHIVED   │
├─────────────────────────────────────────────┤
│ ⚠️ This task is archived and read-only.    │
│    Only admins can modify or reopen it.    │
└─────────────────────────────────────────────┘

[Task details visible]
[Edit form: HIDDEN]
[Comments: VISIBLE but cannot add]
```

### In Task Detail (Admin)
```
┌─────────────────────────────────────────────┐
│ Task Title                    🗄️ ARCHIVED   │
├─────────────────────────────────────────────┤
│ ℹ️ This task is archived. You can reopen   │
│    it to move it back to the board.        │
│                          [🔓 Reopen Task]  │
└─────────────────────────────────────────────┘

[Task details visible]
[Edit form: ENABLED]
[Comments: ENABLED with add form]
```

## Permissions Summary

| Action | Regular User | Admin |
|--------|-------------|-------|
| Complete task | ✅ Yes | ✅ Yes |
| View archived task | ✅ Yes | ✅ Yes |
| View comments/history | ✅ Yes | ✅ Yes |
| Edit archived task | ❌ No | ✅ Yes |
| Add comment to archived | ❌ No | ✅ Yes |
| Reopen archived task | ❌ No | ✅ Yes |
| Delete archived task | ❌ No | ✅ Yes |

## FAQs

**Q: What happens to attachments when a task archives?**  
A: All attachments remain intact and accessible. You can view and download them from the archived task detail page.

**Q: Can I unarchive a task myself?**  
A: No, only admins can reopen archived tasks. Contact your workspace admin if you need a task reopened.

**Q: Will archived tasks show in reports?**  
A: Yes, archived tasks are still in the database and appear in the Done tab. They're just hidden from the active kanban board.

**Q: What if I accidentally mark a task as done?**  
A: Contact an admin to reopen the task. They can restore it to "In Progress" status on the board.

**Q: Can admins prevent tasks from auto-archiving?**  
A: Currently, all tasks automatically archive when marked done. This keeps boards clean and maintains audit trails.

**Q: Are notifications sent when a task is reopened?**  
A: Not currently. Assignees will see the task reappear on the board when they next view the project.

**Q: Can I search for archived tasks?**  
A: Yes, use the Done tab in list view. You can filter by project, assignee, priority, etc.

**Q: How long are archived tasks kept?**  
A: Indefinitely. Archived tasks remain in the system until manually deleted by an admin.

**Q: Can I archive a task without marking it done?**  
A: No, archiving only happens when status changes to "Done". This ensures only completed work is archived.

**Q: What's the difference between archive and delete?**  
A: 
- **Archive**: Task hidden from board but fully accessible in Done tab (reversible)
- **Delete**: Task permanently removed from database (irreversible, admin only)

## Tips for Admins

1. **Regular Reviews**: Periodically check Done tab to ensure correct tasks are archived

2. **Communication**: Let team know why tasks disappear from board (archived, not deleted)

3. **Reopen Sparingly**: Only reopen when truly needed to avoid cluttering board

4. **Use Filters**: When reviewing archived tasks, use project/assignee filters to focus

5. **Audit Trail**: Archived tasks serve as permanent record of completed work

6. **Performance**: Many archived tasks improves board load times by reducing displayed tasks

## Troubleshooting

**Issue**: Task marked done but still on board  
**Fix**: Refresh page. Auto-archive happens on status update; may need page reload to see change.

**Issue**: Can't find completed task  
**Fix**: Go to Tasks → List View → Done tab. Archived tasks only show there, not on board.

**Issue**: Need to edit archived task but not admin  
**Fix**: Contact workspace admin to either reopen task or make edit on your behalf.

**Issue**: Reopen button doesn't appear  
**Fix**: Ensure you're logged in as admin. Button only shows for admin users on archived tasks.

**Issue**: Error when trying to reopen  
**Fix**: Check you have admin permissions. Contact system administrator if issues persist.
