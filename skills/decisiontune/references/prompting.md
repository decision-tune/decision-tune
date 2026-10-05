# How to prompt DecisionTune 1.0

DecisionTune scores the options you give it. It does not think out loud. So the quality of a decision depends on three things you write: the **question**, the **options** and the **state**. This page shows how to write them.

## Start from the actions

Design from what your code can do, not from what the text says.

1. **List the actions your code can take.** For example: send to billing, send to shipping, send to tech support, ask a person.
2. **Write each action's trigger as one sentence.** "Send to shipping when the message is about delivery, lost or damaged packages."
3. **That sentence becomes the question.** One decision point gives one question.
4. **Pick the call type.**
   - `choose`: pick a destination among several actions.
   - `yes_no`: act or do not act.
5. **Put only what a person would look at into the state.** The message text, the subject, maybe the plan tier. Leave out IDs, timestamps and internal fields that a person would ignore.
6. **Describe each option in a short phrase.** Use "Shipping: delivery, lost or damaged packages", not "shipping". Short descriptions give much better results than labels only.
7. **Always offer a way out.** Add an option such as "None of these: the message fits no team" or "Other". Without it, the model must pick a wrong option when none fits.
8. **Ask one judgment per question.** Combine answers in code.
9. **Phrase yes/no so that yes is the risky or actionable case.** "Does this message ask to delete the account?" Then a high `p_yes` means "act", and your threshold guards the action.
10. **Keep math, dates and counting in code.** Compute "days since order" in Python and pass the result, or decide on it in code directly.
11. **Write down the criteria.** Vague questions with no criteria, such as "Is this urgent?", give weak results. Say what urgent means.
12. **Test on real rows and read the failures.** Run a set of your own labeled examples. Look at every wrong answer and every low-confidence one. Fix the option text or the question, then run again.

## Worked design 1: support triage

Actions: send to billing, shipping or tech support, or hold for a person. Also flag refund requests.

```python
from decision_tune import DecisionModel

m = DecisionModel.from_pretrained()

TEAMS = {
    "billing": "Billing: charges, refunds, invoices, payment methods",
    "shipping": "Shipping: delivery, lost or damaged packages, tracking",
    "tech": "Tech support: bugs, crashes, login problems",
    "none": "None of these: spam, praise, or a message for no team",
}

def triage(subject, message):
    state = {"subject": subject, "message": message}
    team = m.choose(state, "Which team should handle this customer message?", TEAMS)
    p_refund = m.yes_no(state, "Is the customer asking for a refund or an exchange?")
    return {
        "team": team["choice"] if team["choice"] != "none" else "human",
        "team_confidence": team["confidence"],
        "refund": p_refund,
    }

print(triage("Where is it?", "My package never arrived."))
```

For a spreadsheet of tickets, the same design is the built-in recipe. Copy it and edit the options:

```bash
decisiontune recipe new my-triage          # writes ~/.decision-tune/recipes/my-triage.json
decisiontune run my-triage tickets.csv     # needs columns subject and message
```

## Worked design 2: tool selection for an agent

Actions: the tools the agent can call, plus "answer directly" as the way out. Describe each tool by what it is for, not by its name.

```python
from decision_tune import DecisionModel

m = DecisionModel.from_pretrained()

TOOLS = {
    "get_weather": "Get the weather forecast for a city and date",
    "send_email": "Send an email to a person",
    "create_calendar_event": "Create a meeting or event in the calendar",
    "none": "No tool: answer the user directly",
}

r = m.choose("What is the weather tomorrow in Paris?", "Which tool should be called?", TOOLS)
tool = r["choice"]
# Low confidence: let the main agent or a person decide instead of calling a tool blindly.
```

DecisionTune picks the tool. It does not fill in the arguments. Extract arguments with your own code or your main model.

Over HTTP, the same call:

```bash
curl -s http://127.0.0.1:8000/decide -H 'Content-Type: application/json' -d '{"state": "What is the weather tomorrow in Paris?", "question": "Which tool should be called?", "options": {"get_weather": "Get the weather forecast for a city and date", "send_email": "Send an email to a person", "create_calendar_event": "Create a meeting or event in the calendar", "none": "No tool: answer the user directly"}}'
```

## Worked design 3: a yes/no guardrail

Action: block a shell command before an agent runs it, or let it through. Yes is the risky case, so a high `p_yes` blocks.

```python
from decision_tune import DecisionModel

m = DecisionModel.from_pretrained()

QUESTION = ("Does this shell command delete files, overwrite data, or change system settings "
            "in a way that cannot be undone?")

def allowed(command, block_at):
    p = m.yes_no(f"Command: {command}", QUESTION)
    return p < block_at   # block_at: pick it from your own labeled commands, not from a guess

# Use a guardrail as one layer. Keep a hard deny list in code for commands you never allow.
```

From the shell:

```bash
decisiontune ask "Does this shell command delete files, overwrite data, or change system settings in a way that cannot be undone?" --state "Command: rm -rf build/" --json
```

## Do and don't

| Don't | Do |
|---|---|
| Options as bare labels: `["billing", "shipping", "tech"]` | Options as short descriptions: `{"shipping": "Shipping: delivery, lost or damaged packages"}` |
| "Is this urgent?" | "Does the customer say the service is down or that they cannot work?" |
| One question that mixes two judgments: "Is this a billing issue and is the customer angry?" | Two questions, one judgment each, combined in code |
| "Is this message safe?" (yes means do nothing) | "Does this message ask us to share another customer's data?" (yes means act) |
| "Was the order placed more than a month ago?" with dates in the state | Compute the age in code and branch on it; ask the model only about the text |
| Only the options that you expect | Add a way out: "None of these: the message fits no team" |

## Things that change the answer

- **The option set.** The model compares the options with each other. Adding, removing or reordering options can change the result. Freeze the option set and test it as a unit.
- **Long states.** Inputs up to 8,192 tokens run, but the model saw mostly shorter text in training. Trim the state to what matters.
- **Probabilities are not calibrated.** Use them to rank and to set thresholds that you checked on your own rows.
