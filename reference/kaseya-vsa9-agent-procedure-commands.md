# Kaseya VSA 9 — Agent Procedure Commands: IF-ELSE-STEP (reference)

> **Sources:** Kaseya VSA 9 help (each command links to `https://help.vsa9.kaseya.com/help/Content/VSA/<id>.htm`)
> - STEP (action) commands index — <https://help.vsa9.kaseya.com/help/Content/VSA/4896.htm>
> - IF (conditional) commands index — <https://help.vsa9.kaseya.com/help/Content/VSA/7883.htm>
> - Combined IF-ELSE-STEP reference — <https://help.vsa9.kaseya.com/help/Content/VSA/674.htm>
> - Agent Procedures feature chapter — <https://help.vsa9.kaseya.com/help/Content/VSA/4549.htm>
>
> **What these are:** the building-block commands of Kaseya **Agent Procedures** (scripts that run ON a
> managed machine). An agent procedure is built from two kinds of steps: **IF** commands (conditional
> tests that branch the procedure) and **STEP** commands (the actions — reboot, run a script, install
> software, edit the registry, manage users/services, move files, etc.).
>
> ⚠️ **STATUS IN MSP AI: REFERENCE ONLY — NOT EXECUTABLE.** MSP AI v1 is read-only; none of these are
> wired as tools/capabilities and the agent CANNOT run them. This doc exists so the assistant *knows
> what Kaseya can do* when planning, advising, or scoping future automation. Almost every command below
> is a **WRITE / destructive** action — if any are ever implemented they go through the
> Capability Console + approval gate (per CLAUDE.md Rule #1), never as an improvised call.
>
> Keywords for search: kaseya vsa9 agent procedure step command automation script reboot powershell
> registry service user file install msi remote action runbook.

---

## IF — conditional commands (branch the procedure)
These are the *tests* used in an `IF … ELSE …` step. They evaluate to true/false and decide which
branch runs; they don't change the machine themselves. (`ELSE` is the else-branch, `true` is the
unconditional/always-run condition.)

| Command | Tests / does | Doc |
|---|---|---|
| `checkVar()` | Test the value of a procedure variable | [4887](https://help.vsa9.kaseya.com/help/Content/VSA/4887.htm) |
| `eval()` | Evaluate an expression / numeric or string comparison | [4888](https://help.vsa9.kaseya.com/help/Content/VSA/4888.htm) |
| `getOS()` | Branch on the machine's operating system | [7877](https://help.vsa9.kaseya.com/help/Content/VSA/7877.htm) |
| `getRAM()` | Branch on the amount of installed RAM | [7868](https://help.vsa9.kaseya.com/help/Content/VSA/7868.htm) |
| `getRegistryValue()` / `get64BitRegistryValue()` | Read a registry value and test it (32/64-bit) | [4886](https://help.vsa9.kaseya.com/help/Content/VSA/4886.htm) |
| `hasRegistryKey()` / `has64BitRegistryKey()` | Test whether a registry key exists (32/64-bit) | [4892](https://help.vsa9.kaseya.com/help/Content/VSA/4892.htm) |
| `isAppRunning()` | Test whether an application/process is running | [4885](https://help.vsa9.kaseya.com/help/Content/VSA/4885.htm) |
| `isServiceRunning()` | Test whether a Windows service is running | [4889](https://help.vsa9.kaseya.com/help/Content/VSA/4889.htm) |
| `isUserActive()` | Test whether the logged-in user is active (not idle) | [7876](https://help.vsa9.kaseya.com/help/Content/VSA/7876.htm) |
| `isUserLoggedin()` | Test whether a user is logged in | [4894](https://help.vsa9.kaseya.com/help/Content/VSA/4894.htm) |
| `isYesFromUser()` | Prompt the user and branch on their Yes/No answer | [4895](https://help.vsa9.kaseya.com/help/Content/VSA/4895.htm) |
| `testFile()` | Test whether a file exists | [4890](https://help.vsa9.kaseya.com/help/Content/VSA/4890.htm) |
| `testFileInDirectoryPath()` | Test for a file at a resolved directory path | [4891](https://help.vsa9.kaseya.com/help/Content/VSA/4891.htm) |
| `else` | The ELSE branch of an IF step | [10548](https://help.vsa9.kaseya.com/help/Content/VSA/10548.htm) |
| `true` | Always-true condition (run the step unconditionally) | [4893](https://help.vsa9.kaseya.com/help/Content/VSA/4893.htm) |

---

# STEP — action commands (do something on the machine)

## Power & session control
| Command | Does | Doc |
|---|---|---|
| `reboot()` | Reboot the managed machine | [4913](https://help.vsa9.kaseya.com/help/Content/VSA/4913.htm) |
| `rebootWithWarning()` | Reboot after showing the logged-in user a warning | [7869](https://help.vsa9.kaseya.com/help/Content/VSA/7869.htm) |
| `logoffCurrentUser()` | Log off the currently logged-in user | [7867](https://help.vsa9.kaseya.com/help/Content/VSA/7867.htm) |
| `updateSystemInfo()` | Refresh the agent's collected system information | [5346](https://help.vsa9.kaseya.com/help/Content/VSA/5346.htm) |
| `pauseProcedure()` | Pause the running procedure for a set time | [4912](https://help.vsa9.kaseya.com/help/Content/VSA/4912.htm) |

## Alarms, alerts & notifications
| Command | Does | Doc |
|---|---|---|
| `alarmsSuspend()` | Suspend alarms for the machine for a period | [10691](https://help.vsa9.kaseya.com/help/Content/VSA/10691.htm) |
| `alarmsUnsuspendAll()` | Resume all suspended alarms | [10692](https://help.vsa9.kaseya.com/help/Content/VSA/10692.htm) |
| `sendAlert()` | Raise a Kaseya alert | [10337](https://help.vsa9.kaseya.com/help/Content/VSA/10337.htm) |
| `sendEmail()` | Send an email from the procedure | [4917](https://help.vsa9.kaseya.com/help/Content/VSA/4917.htm) |
| `sendMessage()` | Display a message dialog to the machine's user | [4918](https://help.vsa9.kaseya.com/help/Content/VSA/4918.htm) |
| `sendURL()` | Open a URL in the user's browser | [4919](https://help.vsa9.kaseya.com/help/Content/VSA/4919.htm) |
| `createEventLogEntry()` | Write an entry to the Windows Event Log | [7930](https://help.vsa9.kaseya.com/help/Content/VSA/7930.htm) |
| `captureDesktopScreenshot()` | Capture a screenshot of the machine's desktop | [7840](https://help.vsa9.kaseya.com/help/Content/VSA/7840.htm) |

## Users & groups
| Command | Does | Doc |
|---|---|---|
| `createLocalUser()` | Create a local user account | [7845](https://help.vsa9.kaseya.com/help/Content/VSA/7845.htm) |
| `createDomainUser()` | Create a domain user account | [7846](https://help.vsa9.kaseya.com/help/Content/VSA/7846.htm) |
| `deleteUser()` | Delete a user account | [7849](https://help.vsa9.kaseya.com/help/Content/VSA/7849.htm) |
| `disableUser()` | Disable a user account | [7850](https://help.vsa9.kaseya.com/help/Content/VSA/7850.htm) |
| `enableUser()` | Enable a user account | [7852](https://help.vsa9.kaseya.com/help/Content/VSA/7852.htm) |
| `changeLocalUserGroup()` | Change a local user's group membership | [7842](https://help.vsa9.kaseya.com/help/Content/VSA/7842.htm) |
| `changeDomainUserGroup()` | Change a domain user's group membership | [7841](https://help.vsa9.kaseya.com/help/Content/VSA/7841.htm) |
| `giveCurrentUserAdminRights()` | Grant the current user local admin rights | [7860](https://help.vsa9.kaseya.com/help/Content/VSA/7860.htm) |
| `impersonateUser()` | Run subsequent steps as a specified user | [4911](https://help.vsa9.kaseya.com/help/Content/VSA/4911.htm) |
| `useCredential()` | Use a stored Kaseya credential for the step | [4921](https://help.vsa9.kaseya.com/help/Content/VSA/4921.htm) |

## Execution (run code/scripts)
| Command | Does | Doc |
|---|---|---|
| `executeFile()` | Execute a file/binary on the machine | [4902](https://help.vsa9.kaseya.com/help/Content/VSA/4902.htm) |
| `executeFileInDirectoryPath()` | Execute a file from a resolved directory path | [4903](https://help.vsa9.kaseya.com/help/Content/VSA/4903.htm) |
| `executeShellCommand()` | Run a shell/command-line command | [4905](https://help.vsa9.kaseya.com/help/Content/VSA/4905.htm) |
| `executeShellCommandToVariable()` | Run a shell command and capture output into a variable | [7854](https://help.vsa9.kaseya.com/help/Content/VSA/7854.htm) |
| `executePowershell()` | Run a PowerShell script/command | [7853](https://help.vsa9.kaseya.com/help/Content/VSA/7853.htm) |
| `executeVBScript()` | Run a VBScript | [7855](https://help.vsa9.kaseya.com/help/Content/VSA/7855.htm) |
| `executeProcedure()` | Call another agent procedure | [4904](https://help.vsa9.kaseya.com/help/Content/VSA/4904.htm) |
| `scheduleProcedure()` | Schedule another agent procedure to run | [4916](https://help.vsa9.kaseya.com/help/Content/VSA/4916.htm) |
| `closeApplication()` | Close a running application | [4897](https://help.vsa9.kaseya.com/help/Content/VSA/4897.htm) |

## Files & directories
| Command | Does | Doc |
|---|---|---|
| `copyFile()` | Copy a file on the machine | [7843](https://help.vsa9.kaseya.com/help/Content/VSA/7843.htm) |
| `copyFileUseCredentials()` | Copy a file using supplied credentials | [7844](https://help.vsa9.kaseya.com/help/Content/VSA/7844.htm) |
| `deleteFile()` | Delete a file | [4898](https://help.vsa9.kaseya.com/help/Content/VSA/4898.htm) |
| `deleteFileInDirectoryPath()` | Delete a file from a resolved directory path | [4899](https://help.vsa9.kaseya.com/help/Content/VSA/4899.htm) |
| `deleteDirectory()` | Delete a directory | [7848](https://help.vsa9.kaseya.com/help/Content/VSA/7848.htm) |
| `getFile()` | Upload a file from the agent to the VSA server | [4907](https://help.vsa9.kaseya.com/help/Content/VSA/4907.htm) |
| `getFileInDirectoryPath()` | Upload a file from a resolved path to the server | [4908](https://help.vsa9.kaseya.com/help/Content/VSA/4908.htm) |
| `writeFile()` | Push a file from the server to the agent | [4923](https://help.vsa9.kaseya.com/help/Content/VSA/4923.htm) |
| `writeFileFromAgent()` | Write a file sourced from another agent | [7879](https://help.vsa9.kaseya.com/help/Content/VSA/7879.htm) |
| `writeFileInDirectoryPath()` | Write a file to a resolved directory path | [4924](https://help.vsa9.kaseya.com/help/Content/VSA/4924.htm) |
| `writeTextToFile()` | Write literal text into a file | [7880](https://help.vsa9.kaseya.com/help/Content/VSA/7880.htm) |
| `writeDirectory()` | Push a whole directory to the agent | [4922](https://help.vsa9.kaseya.com/help/Content/VSA/4922.htm) |
| `transferFile()` | Transfer a file between machines | [7873](https://help.vsa9.kaseya.com/help/Content/VSA/7873.htm) |
| `renameLockedFile()` | Rename a file that is locked (on reboot) | [4914](https://help.vsa9.kaseya.com/help/Content/VSA/4914.htm) |
| `renameLockedFileInDirectoryPath()` | Rename a locked file from a resolved path | [4915](https://help.vsa9.kaseya.com/help/Content/VSA/4915.htm) |
| `createWindowsFileShare()` | Create a Windows file share | [7847](https://help.vsa9.kaseya.com/help/Content/VSA/7847.htm) |
| `removeWindowsFileShare()` | Remove a Windows file share | [7870](https://help.vsa9.kaseya.com/help/Content/VSA/7870.htm) |
| `zipDirectory()` | Zip a directory | [7881](https://help.vsa9.kaseya.com/help/Content/VSA/7881.htm) |
| `zipFiles()` | Zip a set of files | [7885](https://help.vsa9.kaseya.com/help/Content/VSA/7885.htm) |
| `unzipFile()` | Unzip an archive | [7875](https://help.vsa9.kaseya.com/help/Content/VSA/7875.htm) |

## Registry
| Command | Does | Doc |
|---|---|---|
| `getDirectoryPathFromRegistry()` | Read a directory path from a registry value | [4906](https://help.vsa9.kaseya.com/help/Content/VSA/4906.htm) |
| `setRegistryValue()` / `set64BitRegistryValue()` | Set a registry value (32/64-bit) | [4920](https://help.vsa9.kaseya.com/help/Content/VSA/4920.htm) |
| `deleteRegistryKey()` / `delete64BitRegistryKey()` | Delete a registry key (32/64-bit) | [4900](https://help.vsa9.kaseya.com/help/Content/VSA/4900.htm) |
| `deleteRegistryValue()` / `delete64BitRegistryValue()` | Delete a registry value (32/64-bit) | [4901](https://help.vsa9.kaseya.com/help/Content/VSA/4901.htm) |

## Windows services
| Command | Does | Doc |
|---|---|---|
| `startWindowsService()` | Start a Windows service | [7871](https://help.vsa9.kaseya.com/help/Content/VSA/7871.htm) |
| `stopWindowsService()` | Stop a Windows service | [7872](https://help.vsa9.kaseya.com/help/Content/VSA/7872.htm) |
| `disableWindowsService()` | Disable a Windows service | [7851](https://help.vsa9.kaseya.com/help/Content/VSA/7851.htm) |
| `windowsServiceRecoverySettings()` | Configure a service's recovery/failure actions | [7878](https://help.vsa9.kaseya.com/help/Content/VSA/7878.htm) |

## Software install / packages
| Command | Does | Doc |
|---|---|---|
| `installMSI()` | Install a Windows MSI package | [7864](https://help.vsa9.kaseya.com/help/Content/VSA/7864.htm) |
| `uninstallbyProductGUID()` | Uninstall a product by its GUID | [7874](https://help.vsa9.kaseya.com/help/Content/VSA/7874.htm) |
| `installAptGetPackage()` | Install a Linux apt package | [7861](https://help.vsa9.kaseya.com/help/Content/VSA/7861.htm) |
| `installDebPackage()` | Install a Linux .deb package | [7862](https://help.vsa9.kaseya.com/help/Content/VSA/7862.htm) |
| `installRPM()` | Install a Linux .rpm package | [7866](https://help.vsa9.kaseya.com/help/Content/VSA/7866.htm) |
| `installPKG()` | Install a macOS .pkg package | [7865](https://help.vsa9.kaseya.com/help/Content/VSA/7865.htm) |
| `installDMG()` | Install from a macOS .dmg image | [7863](https://help.vsa9.kaseya.com/help/Content/VSA/7863.htm) |

## Variables
| Command | Does | Doc |
|---|---|---|
| `getVariable()` | Read/define a procedure variable | [4910](https://help.vsa9.kaseya.com/help/Content/VSA/4910.htm) |
| `getVariableRandomNumber()` | Generate a random-number variable | [7857](https://help.vsa9.kaseya.com/help/Content/VSA/7857.htm) |
| `getVariableUniversalCreate()` | Create a universal (persistent) variable | [7858](https://help.vsa9.kaseya.com/help/Content/VSA/7858.htm) |
| `getVariableUniversalRead()` | Read a universal (persistent) variable | [7859](https://help.vsa9.kaseya.com/help/Content/VSA/7859.htm) |

## Network / URL fetch
| Command | Does | Doc |
|---|---|---|
| `getURL()` | Download a URL to a file on the agent | [4909](https://help.vsa9.kaseya.com/help/Content/VSA/4909.htm) |
| `getURLUsePatchFileSource()` | Download a URL via the patch file source | [7856](https://help.vsa9.kaseya.com/help/Content/VSA/7856.htm) |

## SQL
| Command | Does | Doc |
|---|---|---|
| `sqlRead()` | Run a read query against a database | [11625](https://help.vsa9.kaseya.com/help/Content/VSA/11625.htm) |
| `sqlWrite()` | Run a write query against a database | [11626](https://help.vsa9.kaseya.com/help/Content/VSA/11626.htm) |

## Logging / procedure control
| Command | Does | Doc |
|---|---|---|
| `writeProcedureLogEntry()` | Write a line to the agent procedure log | [4925](https://help.vsa9.kaseya.com/help/Content/VSA/4925.htm) |
| `comment()` | A no-op comment line in the procedure | [11132](https://help.vsa9.kaseya.com/help/Content/VSA/11132.htm) |

---

## Agent Procedures — feature docs (concepts, not commands)
The broader Agent Procedures chapter (how procedures are authored/managed, not the command verbs):
- [Agent Procedures Overview](https://help.vsa9.kaseya.com/help/Content/VSA/4551.htm)
- [Manage Procedures](https://help.vsa9.kaseya.com/help/Content/VSA/41467.htm)
- [Installer Wizard](https://help.vsa9.kaseya.com/help/Content/VSA/41469.htm) — build software-install procedures
- [File Transfer](https://help.vsa9.kaseya.com/help/Content/VSA/41470.htm)
- [Administration](https://help.vsa9.kaseya.com/help/Content/VSA/41471.htm)

> Agent procedures are authored/scheduled in Kaseya, then run on machines. The REST API can *list, run,
> schedule, and delete* them — see `AgentProcedure` in
> [the REST API catalog](kaseya-vsa9-rest-api-endpoints.md) (`/automation/agentprocs...`). A future MSP AI
> "run an approved procedure" capability would combine those (a 🔴 write, gated by approval).

---

*92 commands total: 15 IF (conditional) + 77 STEP (action). Captured from the VSA 9 help "In This
Section" indexes (4896, 7883, 674) on 2026-06-03. If Kaseya adds/renames commands, re-fetch the source
URLs above and update this file.*
