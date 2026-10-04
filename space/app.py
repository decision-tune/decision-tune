import time

import gradio as gr
import pandas as pd

from decision_tune import DecisionModel

m = DecisionModel.from_pretrained(backend="torch", device="cpu", yes=True)


def decide(kind, state, question, options):
    opts = [o.strip() for o in (options or "").splitlines() if o.strip()]
    if kind == "Choice" and len(opts) < 2:
        raise gr.Error("A choice question needs at least 2 options, one per line.")
    t = time.perf_counter()
    if kind == "Choice":
        r = m.choose(state, question, opts)
        text, probs = f"{r['choice']}  (confidence {r['confidence']:.1%})", r["probabilities"]
    else:
        p = m.yes_no(state, question)
        text, probs = f"{'yes' if p >= 0.5 else 'no'}  (P(yes) = {p:.1%})", {"yes": p, "no": 1 - p}
    ms = (time.perf_counter() - t) * 1000
    df = pd.DataFrame({"option": list(probs), "probability": list(probs.values())})
    return text, df, f"{ms:.0f} ms on a shared CPU (one encoder pass)"


with gr.Blocks(title="DecisionTune 1.0") as demo:
    gr.Markdown("# DecisionTune 1.0\nA 395M decision model. Pick **Choice** to choose among options (one per line), "
                "or **Yes/No** for P(yes). Short option descriptions work better than bare labels. [Model card](https://huggingface.co/decision-tune/decisiontune-1.0) · "
                "`pip install decision-tune` · Apache-2.0")
    kind = gr.Radio(["Choice", "Yes/No"], value="Choice", label="Question type")
    state = gr.Textbox(label="State (the context to decide on)", lines=4, value="The order arrived broken.")
    question = gr.Textbox(label="Question", value="Which team should handle this customer message?")
    options = gr.Textbox(label="Options (one per line; ignored for Yes/No)", lines=4, value="Billing: charges, refunds, invoices\nShipping: delivery, lost or damaged packages\nTech support: bugs, crashes, login problems")
    go = gr.Button("Decide", variant="primary")
    out = gr.Textbox(label="Decision")
    plot = gr.BarPlot(x="option", y="probability", y_lim=[0, 1], label="Probabilities")
    lat = gr.Textbox(label="Latency")
    go.click(decide, [kind, state, question, options], [out, plot, lat], api_name="decide")

if __name__ == "__main__":
    demo.launch()
