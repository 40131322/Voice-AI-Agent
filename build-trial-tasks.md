# In-Person Build Trial — Task List
**Voice AI Agent for Medical Office Intake & Scheduling**

> **Primary expectation:** Finish with a working end-to-end demo. Prioritize a complete, demonstrable flow over extra features, and reduce scope when necessary.
>
> **Objective:** Working with the team, extend OpenPoke into a natural voice agent for doctor's-office intake, urgency routing, scheduling, tool calls, and human handoff.

## Workflow (target flow)
1. Caller connects
2. Identify caller + reason for visit
3. Emergency screen
   - **Clearly urgent →** direct caller to 911 + stop booking
   - **Not emergent →** continue
4. Focused intake + clarifying questions
5. Assign priority / urgency level
6. Check appointment availability (tool)
7. Offer options + confirm booking

*Human handoff available at any point: caller requests it, uncertainty, sensitive situation, or tool failure.*

## Pre-work
- [ ] Review repo: github.com/shlokkhemani/openpoke
- [ ] Read: shloked.com/writing/openpoke
- [ ] Understand OpenPoke's interaction-agent and execution-agent model
- [ ] Be prepared to discuss the execution-agent overload problem and how to avoid excessive tool or agent exposure
- [ ] Review the basic voice AI loop: speech-to-text, reasoning/tool calls, text-to-speech, interruptions, latency, turn-taking

## What to build
- [ ] **Voice-first intake flow** that identifies the caller and gathers the minimum useful reason-for-visit information
- [ ] **Triage layer** that recognizes possible emergencies. For clearly urgent danger, instruct the caller to call 911 immediately; do not continue routine booking
- [ ] **Priority decision** for non-emergency cases (same-day, soon, or routine follow-up). May be rule-based or model-assisted, but must be explainable and conservative
- [ ] **Scheduling dialogue** that checks availability via a tool, proposes options, handles conflicts, and confirms a date/time that works for the caller
- [ ] **Human handoff path** for when the caller requests it, the agent is uncertain, the situation is sensitive, or a tool fails

## Expected behaviour
- [ ] Sound natural: ask one question at a time, acknowledge answers, avoid robotic repetition, allow the caller to interrupt or correct
- [ ] Adapt the conversation instead of forcing every caller through the same script
- [ ] Use tools intentionally. Mocked tools are acceptable for patient lookup, appointment availability, booking, escalation, and human transfer
- [ ] Confirm critical details before acting, especially identity, callback number, appointment time, and escalation
- [ ] Do not diagnose, prescribe, or claim clinical certainty. The agent supports intake and routing only

## Deliverables
- [ ] **Most important:** a working end-to-end demo by the end of the trial. Scope down rather than leave the core flow unfinished
- [ ] At least three demonstrated scenarios: routine booking, higher-priority intake, and emergency or human escalation
- [ ] A short README or architecture note describing the flow, tools, prompts, safety logic, and key trade-offs
- [ ] A brief walkthrough of what you'd improve with more time — especially latency, reliability, evaluation, privacy, and agent/tool overload

## How it will be evaluated
- Product judgment and empathy for the caller
- Conversation quality and voice UX
- Technical design, code quality, and effective use of the existing repo
- Safety, uncertainty handling, and quality of human escalation
- Ability to collaborate, make sensible scope decisions, and deliver a working demo within the trial

---
*Candidate Build Brief · Confidential*
