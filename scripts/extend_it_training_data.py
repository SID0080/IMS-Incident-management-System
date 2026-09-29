"""
extend_it_training_data.py

Expands the IT department training samples for categorizer.py with:
  1. REAL_INSPIRED_SAMPLES (32) — genuine equipment-failure tickets sourced from
     the Zenodo "Classification of IT Support Tickets" dataset (2229 real,
     anonymized helpdesk tickets, CC BY 4.0, DOI 10.5281/zenodo.7648117).
     Non-English originals were translated to English; minor grammar cleanup
     applied; original anonymized [TAG] placeholders removed. These are
     authentic equipment-failure phrasing from real users, not synthetic.

  2. SYNTHETIC_SAMPLES (116) — freshly generated equipment-failure incidents
     covering printers, laptops, desktops, monitors, servers, network
     hardware, scanners, projectors, UPS units, keyboards/mice, docking
     stations, and mobile devices — matching the style of your existing
     100-sample IT category.

Combined this adds 148 new IT-category training examples (32 real + 116
synthetic), on top of your existing 100, bringing IT to 248 total samples —
the deepest category in your dataset, which matches your project's stated
focus on IT equipment failure.

USAGE:
  1. Open scripts/train_categorizer.py
  2. Find your existing IT_SAMPLES list
  3. Append: IT_SAMPLES += REAL_INSPIRED_SAMPLES + SYNTHETIC_SAMPLES
     (or copy these lists in directly)
  4. Re-run: python scripts/train_categorizer.py
  5. Check the printed cross-validation accuracy — should hold steady or
     improve since these are clean, well-labeled additions
"""

# ════════════════════════════════════════════════════════════════
# 1. REAL-INSPIRED SAMPLES — sourced from real anonymized IT tickets
#    (Zenodo dataset, Pereira 2022, CC BY 4.0)
# ════════════════════════════════════════════════════════════════

REAL_INSPIRED_SAMPLES = [
    "Printer at my desk is hesitating and stalling when printing documents.",
    "The office printer is showing offline status and will not respond after multiple restarts.",
    "Documents are not scanning from the copier through to email as expected.",
    "My computer is freezing constantly and running extremely slow today.",
    "The scanner on the network printer will not work no matter what I try.",
    "Printer in the office is completely broken and does not print anything.",
    "The local printer in my area has disappeared from my laptop and needs to be reconnected.",
    "Unable to print labels from the dedicated label printer in the warehouse.",
    "My laptop is malfunctioning and keeps deleting files unexpectedly.",
    "My Dell laptop will not connect to the network drive no matter how many times I retry.",
    "The wireless network driver on my laptop fails every time I unplug and reconnect it.",
    "We have a temperature logging device that has malfunctioned and the software needed to read it is missing.",
    "Unable to print to the printer despite trying several times from different documents.",
    "Need help connecting to the printer in the office, not urgent but it has stopped responding.",
    "The scan function on my machine is not going through, getting stuck every time.",
    "Requesting installation of a new printer for the desk since the old unit failed.",
    "The printer at my desk is hesitating again and now it also lost network connectivity.",
    "My notebook computer is locking up and becoming unresponsive throughout the day.",
    "The printer in the production room keeps spooling endlessly and never completes a job.",
    "BitLocker unlock failed on my workstation and I cannot get past the boot screen.",
    "My company phone is physically broken and the screen no longer responds to touch.",
    "My computer keeps freezing and is extremely slow, particularly when opening folders.",
    "The printer shows an error every time I try to scan a document into it.",
    "My laptop will not power on properly and keeps restarting on its own.",
    "Need a new wireless mouse and keyboard installed, the current set has stopped responding.",
    "The Zebra label printer is no longer printing labels correctly, output is blank.",
    "There is an error showing on my desktop printer that I do not understand, nothing prints.",
    "My computer keeps shutting down on its own throughout the work day for no clear reason.",
    "Printer driver needs reinstalling on the new machine, it currently prints garbled text.",
    "The printer is jammed repeatedly when scanning incoming faxes.",
    "Connection error when trying to print, the device shows as unreachable on the network.",
    "Monitor connected to my workstation has gone completely black and will not display anything.",
]


# ════════════════════════════════════════════════════════════════
# 2. SYNTHETIC SAMPLES — fresh equipment-failure incidents
# ════════════════════════════════════════════════════════════════

SYNTHETIC_SAMPLES = [
    # ── Printers (24) ──────────────────────────────────────────
    "Office printer on the third floor is jamming on every single page and needs immediate repair.",
    "Color printer toner cartridge has leaked and damaged several recent print jobs.",
    "Network printer in the finance department keeps disconnecting and reconnecting randomly.",
    "Wireless printer connection drops every few minutes making it impossible to print large documents.",
    "Printer fuser unit appears to have failed, paper is coming out with the toner not properly fused.",
    "HP LaserJet in the reception area is making a loud grinding noise and refuses to print.",
    "Printer paper tray sensor is malfunctioning and the device thinks it is always out of paper.",
    "Multi-function printer scanner glass is cracked and producing distorted scanned images.",
    "Printer driver crashes the print spooler service every time a print job is sent.",
    "Industrial label printer in the warehouse has stopped feeding labels correctly.",
    "Office printer display panel is blank and the device does not respond to any button press.",
    "Printer is printing faded and streaky output even after replacing the toner cartridge.",
    "Network printer queue is stuck with hundreds of pending jobs that will not clear.",
    "Receipt printer at the front desk is not cutting paper properly and jams constantly.",
    "Color laser printer is producing prints with incorrect color calibration, everything looks magenta.",
    "Printer power supply unit appears to have burned out, device will not turn on at all.",
    "Plotter printer used by the design team is producing misaligned prints with visible banding.",
    "Printer USB connection keeps disconnecting intermittently causing failed print jobs.",
    "Thermal printer used for shipping labels is overheating and shutting down mid-print.",
    "All-in-one printer fax function has stopped working though printing and scanning still work.",
    "Printer duplexer unit for double-sided printing has jammed and cannot be cleared.",
    "Office printer ink cartridge is not recognized by the printer despite being brand new.",
    "Networked printer has an IP conflict causing intermittent connection failures across the floor.",
    "Printer cooling fan is making excessive noise and the device shuts down after a few minutes of use.",

    # ── Laptops & Desktops (28) ─────────────────────────────────
    "Company laptop screen has multiple dead pixels and a visible crack across the display.",
    "Desktop computer in the conference room will not boot past the BIOS screen.",
    "Laptop battery no longer holds charge and dies within ten minutes of being unplugged.",
    "Workstation hard drive is making clicking noises and the system has become extremely slow.",
    "Laptop keyboard has several keys that have stopped registering keystrokes entirely.",
    "Desktop tower power supply fan has stopped spinning and the machine overheats quickly.",
    "Laptop hinge is broken and the screen will not stay upright during normal use.",
    "Workstation RAM appears to be failing, system displays blue screen errors multiple times daily.",
    "Laptop charging port is loose and the device will not charge unless the cable is held at an angle.",
    "Desktop computer randomly restarts during video calls and heavy application use.",
    "Laptop trackpad has become unresponsive and clicks are not being registered correctly.",
    "Workstation graphics card is producing visual artifacts and screen flickering during use.",
    "Laptop fan is constantly running at full speed even during light usage, device runs very hot.",
    "Desktop motherboard appears to have failed after a recent power outage in the building.",
    "Laptop webcam has stopped working and shows a black screen during video conferences.",
    "Workstation USB ports have stopped recognizing any connected devices including the mouse.",
    "Laptop speakers have stopped producing sound even though headphones work fine.",
    "Desktop computer case fan is rattling loudly and vibrating against the side panel.",
    "Laptop touchpad buttons are sticking and require excessive force to register a click.",
    "Workstation SSD drive is throwing read/write errors and several files have become corrupted.",
    "Laptop display flickers intermittently and occasionally goes completely black for a few seconds.",
    "Desktop tower will not power on at all, no lights or fan activity when the button is pressed.",
    "Laptop overheats within minutes of opening any application and shuts down automatically.",
    "Workstation CMOS battery has died causing the system clock to reset every time it is powered off.",
    "Laptop hard drive bay is making a loud whirring noise on startup before the system boots.",
    "Desktop computer monitor cable connector pins appear bent and the display shows no signal.",
    "Laptop lid sensor is malfunctioning causing the screen to go to sleep while still in use.",
    "Workstation cooling system has failed and the CPU is running at dangerously high temperatures.",

    # ── Monitors & Peripherals (22) ─────────────────────────────
    "External monitor connected via HDMI shows a flickering image with horizontal lines across the screen.",
    "Dual monitor setup has stopped detecting the second screen after a recent Windows update.",
    "Monitor stand is broken and the screen will not stay at a stable height or angle.",
    "Office monitor backlight has failed, the display is extremely dim and hard to read.",
    "Wireless keyboard has become unresponsive and requires battery replacement multiple times a week.",
    "Wireless mouse cursor is jumping erratically across the screen during normal use.",
    "USB docking station has stopped charging connected laptops despite being plugged in.",
    "Conference room projector bulb has burned out and needs immediate replacement.",
    "Projector in the training room is displaying a distorted, blurry image despite focus adjustments.",
    "External monitor power button is unresponsive and the display will not turn on.",
    "Docking station ports have stopped working, none of the connected peripherals are recognized.",
    "Office webcam has developed a persistent green tint affecting all video call quality.",
    "Headset microphone has stopped transmitting audio during calls though the headphones still work.",
    "Monitor display has a permanent burn-in mark visible on the screen during all applications.",
    "USB hub connected to the workstation has stopped supplying power to connected devices.",
    "Conference room speakerphone unit has developed a persistent static noise during calls.",
    "Ergonomic keyboard has several sticky keys that require excessive pressure to type.",
    "External hard drive connected to the workstation is not being recognized by the operating system.",
    "Monitor arm mount has come loose and the screen keeps drooping during the workday.",
    "Barcode scanner at the warehouse desk has stopped reading barcodes accurately.",
    "Office phone handset has a cracked casing and the speaker crackles during calls.",
    "Touchscreen monitor in the lobby kiosk is unresponsive to touch input in the lower half of the screen.",

    # ── Network & Server Hardware (20) ─────────────────────────
    "Network switch in the server room has several ports that have stopped passing traffic.",
    "Office WiFi access point keeps dropping connections every few minutes throughout the floor.",
    "Server rack cooling fan has failed causing temperatures in the server room to rise dangerously.",
    "Ethernet cable running to the marketing department appears to be physically damaged.",
    "Network router in the branch office is overheating and randomly rebooting itself.",
    "UPS battery backup unit in the server room is beeping continuously and showing a fault light.",
    "Server hard drive in the storage array has failed and triggered a RAID degraded alert.",
    "Firewall appliance hardware has stopped responding and is blocking all outbound traffic.",
    "Network patch panel connection to the second floor has intermittent signal loss.",
    "Server power supply unit has failed, redundant supply is now carrying the full load.",
    "Wireless access point antenna appears physically damaged after recent maintenance work.",
    "Network attached storage device is making clicking noises and showing disk errors.",
    "Server room temperature sensor is malfunctioning and triggering false overheating alerts.",
    "Core network switch has lost power and the redundant unit has not failed over correctly.",
    "Modem in the remote office location has stopped syncing with the ISP connection.",
    "Server blade chassis cooling system is making unusual grinding noises during operation.",
    "Network cable termination at the wall jack has come loose causing intermittent connectivity.",
    "Backup power generator for the server room failed to start during the last outage test.",
    "Load balancer hardware appliance has stopped distributing traffic evenly across servers.",
    "Server room access control card reader has malfunctioned and will not unlock the door.",

    # ── Mobile Devices (14) ─────────────────────────────────────
    "Company-issued mobile phone screen is cracked and the touch response has become unreliable.",
    "Tablet device used for inventory scanning will not hold a charge for more than an hour.",
    "Company phone camera has stopped focusing and all photos come out blurry.",
    "Mobile device charging port is damaged and the cable falls out during charging.",
    "Tablet touchscreen has become unresponsive in several areas making the app unusable.",
    "Company phone speaker has stopped working, calls can only be heard through headphones.",
    "Mobile scanner device used in the warehouse has a cracked screen and erratic scanning behavior.",
    "Tablet battery is swelling and pushing against the back casing, device needs replacement.",
    "Company phone power button is stuck and the device cannot be turned off or restarted normally.",
    "Field service tablet has a broken charging dock and will not connect to power.",
    "Mobile device fingerprint sensor has stopped recognizing registered fingerprints.",
    "Company phone volume buttons have stopped responding making calls difficult to manage.",
    "Tablet used in the field is overheating rapidly and shutting down during outdoor use.",
    "Mobile hotspot device battery drains completely within thirty minutes of being unplugged.",

    # ── Misc Equipment (8) ──────────────────────────────────────
    "Conference room video conferencing camera unit has stopped panning and tilting on command.",
    "Time clock device at the warehouse entrance has stopped registering employee badge scans.",
    "Electronic whiteboard in the meeting room has a malfunctioning touch surface in one corner.",
    "Smart card reader at the IT helpdesk has stopped reading employee access cards.",
    "Digital signage display in the lobby has gone black and will not respond to remote restart.",
    "Biometric fingerprint scanner at the data center entrance is rejecting valid fingerprints.",
    "Cash drawer connected to the point-of-sale terminal will not open electronically.",
    "Environmental sensor monitoring server room humidity has stopped reporting any readings.",
]


# ════════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print(f"REAL_INSPIRED_SAMPLES: {len(REAL_INSPIRED_SAMPLES)} samples")
    print(f"SYNTHETIC_SAMPLES:     {len(SYNTHETIC_SAMPLES)} samples")
    print(f"TOTAL NEW IT SAMPLES:  {len(REAL_INSPIRED_SAMPLES) + len(SYNTHETIC_SAMPLES)}")
    print()
    print("Add these to your existing IT_SAMPLES list in train_categorizer.py,")
    print("then re-run: python scripts/train_categorizer.py")
