# Story bible

The source of truth for all story text is `data/story/beats.yaml`. **Every line there is hand-written. Do not regenerate it with an LLM.** LLMs are allowed only for incidental filler, and in this build even the filler is hand-written.

## Tone
Restrained, human, never melodramatic. The distance does the work. Nobody says "I miss you". Nobody cries on the page. Danger is never explained to the child.

## People
**Cmdr. Imani Reyes.** Callsign *Meridian*. A professional first; warm in few words. She protects Lila from the mission and the mission from Lila, and doesn't always manage both.

**Lila Reyes, 7.** Houston. Short sentences, concrete objects (a loose tooth, pancakes, a library book, an envelope). Curious, a little annoyed, funny without trying. She invents the rule that drives the whole story: say "over" when you're done.

**Theo Reyes.** Lila's dad. Practical, brief, kind. He's the only person who talks to Lantern as family.

**TERN.** The onboard AI. Calm, exact, kind, very short. It never pretends to be human and never comforts with invented facts. Every number it says comes from a tool.

**Lantern Control / Flight.** Player A. Their words are their own.

**FIDO.** The ground assistant. Always says how old its numbers are.

## The instrument: "over"
1. **Born** in Beat 1, when a 12-second round trip makes them talk over each other: "you have to say over when you're done, like the real ones. So I know."
2. **Abandoned** in the silence, night 8: "I'm not going to say over anymore because you can't hear it anyway."
3. **Returned** on night 10: "I'm saying it again. In case. Over."
The player may notice none of this. It works anyway.

## The six beats

| # | Beat | Scene | Staging |
|---|---|---|---|
| 1 | **The last live goodnight** | s2, Day 5, 8 pm Houston, 6.1 s each way | Lila's lines are timed against the delay so they overlap Imani's replies. The player feels the gap before anyone names it. |
| 2 | **Lila's first confused message** | s3, Day 62, 78 s each way | "Where is it right now? Is it in space?" A note points at the in-flight lane: Imani's last goodnight *is* in space, a packet halfway home. Imani's answer offers: "It's about halfway between us right now… They always get there." |
| 3 | **The day Imani can't answer in time** | s4, Day 98, 172 s each way | A coolant pump fault lands 20 minutes before bedtime. Lila waits up. If the reply reaches Houston before 8:20 pm: "She heard you. Barely." If not: "Asleep on the couch holding the tablet. I'll play yours for her at breakfast." The goodnight becomes a good morning. |
| 4 | **A storm night** | s5, the storm | Lila sends a drawing of the ship "with more windows". Theo calls Lantern ("Is she okay?"), so Lantern must answer a family with four-minute-old data. From the shelter Imani says: "It's like a blanket fort." |
| 5 | **The silence** | s6–s7 | Before the moratorium Imani explains: "The Sun is going to be between us for a while. Nothing is broken. It's just in the way." Lila's reply ("How long is a while?") arrives during the time skip. Imani's answer, "About twelve nights. Count them for me", is recorded in the dark and waits in the ship's store. Eleven nights of Lila queue in Houston. The audio drops to room tone. |
| 6 | **The flood** | s8 | Contact returns and Lantern's store flushes. Eleven nights from Lila plus one from Theo arrive within seconds of each other, 21.8 minutes later, each stamped with the night it was recorded. The player chooses the order. After the last one: "I heard every one. Every single one. Keep the tooth. Over." It crosses space; the screen holds on the packet. |

## The eleven nights (dates in the default plan)
Created at 8:00 pm CDT on Oct 2–12, 2021, inside the real blackout (2021-10-02 11:43 UTC → 10-13 23:26 UTC):

1. Goodnight, Mom. Dad says you can't hear this yet but I'm saying it anyway. Over.
2. We had pancakes for dinner. Dad says don't tell you. Over.
3. My tooth is loose. The bottom one. Over.
4. Ms. Okafor asked what you do and I said you're on the other side of the Sun. She said that's very far. I said I know. Over.
5. I'm not tired. Goodnight. Over.
6. The tooth came out at lunch. I'm keeping it so you can see it. It's in an envelope. Over. *(Theo, later that night: "We're okay. She's keeping a log. You'll see. Love, T.")*
7. *(Oct 8, the day of conjunction)* Dad showed me on the computer where the Sun is. It's right in the middle. That's a dumb place for it. Over.
8. I'm not going to say over anymore because you can't hear it anyway.
9. Goodnight.
10. I'm saying it again. In case. Over.
11. Dad says the Sun is moving out of the way. Can you hear me yet? Over.

## Recording real voices
Text-only is the default and works. If you record voices: use adult performers (or a team member's child **with written parental consent**), keep takes plain, no music under them, and store them in `clients/shared/audio/letters/<beat-id>.ogg`. Never use a synthetic child voice. It reads as uncanny and undercuts the tone.

## Things the story never does
- Never threatens Imani's life on screen.
- Never lets TERN say anything ungrounded to make a moment land.
- Never scores the letters.
