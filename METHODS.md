# GPT-6 Astra Geometry Dash challenge — method draft

This is a tool-assisted experiment in the paid Steam version of Geometry Dash.
The selected challenge is Clubstep, Theory of Everything 2 and Deadlocked, plus
Society, Thinking Space II and Amethyst (the community ranking frozen on
September 18, 2026). Clubstep is verified complete and published. The other five selected challenges remain unfinished.

The controller uses an original local Geode bridge. It can read the player
position, velocity, movement mode and nearby collision geometry. It sends
ordinary jump presses and releases to the original game and leaves the game's
collision and damage checks enabled. It does not import another player's input
macro, move the player, change unlocks, disable collisions or set completion flags.

The game freezes while the controller plans. Each advance runs the original
scheduler at 240 physics ticks per game second, with no more than four ticks per
recorded frame. Input changes can still occur on an individual tick. This is not
a demonstration of human-speed visual reactions. Planning pauses and slow
execution can be shortened in the edit, with that method disclosed in the video.

The original cube planner predicts a short trajectory from observed movement and
collision rectangles and native circle radii. These predictions are approximate and are never written
into the game. The game determines whether an input survives. An early attempt
failed because an observation interval skipped a brief platform landing; the
next version observes more frequently while descending. Failures remain in the
source footage and attempt journal.

Upside-down cube control was calibrated during Dry Out against recorded falls,
landings and jumps. It reflects the local prediction coordinates to reuse the
trajectory model; the live game continues to apply its original gravity and
collisions. Unmeasured movement modes and mechanisms still require a new plan.

Clubstep added measured yellow, pink and blue ring behavior, including changes
of gravity, and blue pads approached in either gravity direction. The controller
can join platform jumps and ring taps into a route. It can reuse the remaining
route after checking the actual position, velocity, gravity, attempt identity
and freshly observed obstacles. A mismatch triggers a new plan. Controllers
share the record of used rings so a handoff does not deliberately repeat a tap.
The original game still decides every collision and completion.

The first two Clubstep failures exposed different planning errors: using a
rectangle for a circular saw, and reaching a platform without checking the exit.
The predictor now uses the game's effective circle radius and checks the route
after a landing. Gravity-portal behavior is compared with recorded movement.
Read-only death telemetry identifies the object hit; it does not intercept or
prevent the original death. Attempts one and two ended at 18.44% and 20.80%.

The ship controller predicts short sequences of holds and releases using lift
and fall measured in the game. Inverted flight was calibrated separately;
its steering changes with vertical speed. It reobserves between decisions and stops at
unsupported modes or a route it cannot safely predict. Successful sections may
be replayed from this task's own input journal after a death, with position and
velocity checked against the earlier run. These learned replays are part of the
tool-assisted method, not fresh real-time decisions or third-party macros.

Automatic pad contacts are distinct from deliberate ring taps. A fresh plan
must not count a pad again simply because the player still overlaps it. Recorded
corner landings also refine the local block model. These remain approximate
predictions; the original game determines survival. On attempt three, Clubstep's
first ship section and the following five-ring sequence were cleared before
the miniature-cube transition at 36.60%. Miniature jumping and pink-pad launches
were then measured separately. A later three-ring plan missed the fourth gravity
ring and stranded the cube above the level; that attempt was manually abandoned.
The miniature planner now requires a landing after the ring sequence. That was an unsuccessful attempt; the eventual Clubstep clear came on attempt seven.

Attempt four cleared the miniature-cube rings and reached the ball section.
A low route from two manual flips could not connect safely to the next rings,
so that attempt was abandoned. Attempt five used the automatic yellow pad and
cleared the ball section. Ball gravity, flips, all three ring colors, yellow
and pink pads were measured from ordinary recorded inputs. The ball scene's
fixed ceiling also needed to be represented in the local predictor. The run
then cleared the miniature UFO section. Its flap, gravity transition and terminal
fall speed were checked against recorded movement. Ship calibration subsequently
climbed too high for the descending corridor, so attempt five was manually
abandoned alive at 56.34%. Attempt six cleared that ship on a lower line but
then reached a ring-route dead end at 62.97%. A rectangle-only record had conflated
two differently colored rings at the same position. Attempt seven verified that
a yellow tap followed by a blue tap activates both original rings separately.
The revised search required a complete route to the next ship portal. That
sequence passed, and the run reached the second UFO section at 68.40%. Miniature
inverted ship steering was also measured in four recorded input sequences.
Attempt seven subsequently cleared both remaining ship sections, the second UFO
section, and the last ring sequence. A UFO floor landing had initially been
misclassified as a crash by the predictor; ordinary recorded movement confirmed
safe contact. The final run passed the original Level Complete screen and saved
100% in normal mode. Clubstep took seven recorded attempts overall: two deaths,
four manual resets and the successful clear. The original final screen displays
Attempts 1 after menu re-entry, and the published edit explicitly explains the
overall count. No Secret Coins were collected in that Clubstep clear.

The Clubstep milestone edit is 112.066667 seconds at 60 fps with zero audio,
16 concise public strategy captions, a title card and an ending recap. The
original successful gameplay plays at normal game speed; planning pauses are
removed using acknowledged game ticks. The video fully decoded before upload.
- Clubstep video: https://youtu.be/aGoAvYvWUSU
- Six-post milestone thread: https://x.com/imjustnewatai/status/2100972237272629679

The original Theory of Everything 2 menu requires 20 Secret Coins. Eleven are
currently banked, so nine more must be earned through completed prerequisite
levels before its first attempt. Base After Base is the current prerequisite.

The recorder copies only the game's rendered image. FFmpeg receives video only;
there is no microphone, desktop-audio or voiceover source. A frame is acknowledged
after the encoder reports processing it. The bridge waits for that acknowledgment
before another advance, and stops if the recording lease expires. Each command
and reply has a unique identifier and is tied to the exact game process, avoiding
blind retries after a lost response.

On-screen captions describe the current action or strategy in concise language.
They are public explanations, not a transcript of private internal reasoning.

Before publication, each claimed completion must have visible in-game evidence,
matching normal-mode state, an input journal and a finalized video that fully
decodes with zero audio tracks. Only original harness source, sanitized results
and game footage will be shared. Game assets, dependencies, account screens,
personal paths, credentials and private raw logs are excluded.

Prerequisite progress verified September 18, 2026: Stereo Madness with two
Secret Coins, Back On Track with three, Polargeist with three, and Dry Out with
three, for eleven earned coins. Clubstep is now visibly unlocked. These four
levels are unlock prerequisites, not the six selected challenge levels.
Polargeist took four recorded attempts overall; its final screen says Attempts 1
because the game resets that displayed counter on menu re-entry. Its proof title
and caption disclose the overall count. Dry Out completed on its first recorded
attempt, with 108 jumps and all three coins. All four prerequisite edits fully
decoded with zero audio tracks and are now public on YouTube. Each shows normal
game speed with planning pauses removed. The Dry Out edit is 1:35 and includes
nine short public strategy captions.

- Stereo Madness: https://youtu.be/Sxmj7L18jeo
- Back on Track: https://youtu.be/x4_27lbXZTE
- Polargeist: https://youtu.be/5lOdKh3fU4Q
- Dry Out: https://youtu.be/WbWDIBJamfM
- Publication thread: https://x.com/imjustnewatai/status/2100945309438279858
