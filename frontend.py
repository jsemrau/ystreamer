"""
TidalCycles Frontend - Gradio UI
Provides web interface for the TidalCycles AI Music Generator
"""

import gradio as gr
import tidal_backend as backend

# Create Gradio interface
with gr.Blocks(title="TidalCycles AI Music Generator") as demo:
    gr.Markdown("# 🎵 TidalCycles AI Music Generator")
    gr.Markdown("Generate live-coded music using AI agents and TidalCycles")
    
    with gr.Row():
        with gr.Column(scale=1):
            gr.Markdown("### Settings")
            
            with gr.Group():
                test_mode_checkbox = gr.Checkbox(
                    label="Test Mode", 
                    value=False,
                    info="Use test sample instead of AI generation"
                )
                
                composer_model_dropdown = gr.Dropdown(
                    choices=[
                        "ollama/qwen2.5-coder:14b",
                        "ollama/qwen2.5-coder:32b",
                        "ollama/deepseek-coder-v2:16b",
                        "ollama/codestral:22b",
                        "gpt-4o",
                        "gpt-4o-mini",
                        "claude-3-5-sonnet-20241022",
                        "claude-3-5-haiku-20241022"
                    ],
                    value="ollama/qwen2.5-coder:14b",
                    label="Composer Model",
                    info="AI model for generating TidalCycles code"
                )
                
                director_model_dropdown = gr.Dropdown(
                    choices=[
                        "ollama/qwen2.5-coder:14b",
                        "ollama/qwen2.5-coder:32b",
                        "ollama/deepseek-coder-v2:16b",
                        "ollama/codestral:22b",
                        "gpt-4o",
                        "gpt-4o-mini",
                        "claude-3-5-sonnet-20241022",
                        "claude-3-5-haiku-20241022"
                    ],
                    value="ollama/qwen2.5-coder:14b",
                    label="Creative Director Model",
                    info="AI model for suggesting musical directions"
                )
                
                director_interval_slider = gr.Slider(
                    minimum=3,
                    maximum=30,
                    value=8,
                    step=1,
                    label="Creative Director Interval (seconds)",
                    info="Time between Creative Director suggestions"
                )
                
                task_input = gr.Textbox(
                    label="Musical Goal",
                    placeholder="Example: create a relaxing atmospheric soundscape",
                    lines=5,
                    value="create a relaxing atmospheric low-tempo song to cure ADHD",
                    info="The overall creative direction for this musical piece"
                )
            
            with gr.Row():
                start_btn = gr.Button("▶ Start", variant="primary", size="lg")
                stop_btn = gr.Button("⏹ Stop", variant="stop", size="lg", interactive=False)
        
        with gr.Column(scale=2):
            gr.Markdown("### Output")
            status_text = gr.Textbox(label="Status", interactive=False)
            
            with gr.Row():
                with gr.Column(scale=1):
                    song_context_output = gr.Textbox(
                        label="Current Song Context (Playing Code)", 
                        lines=25, 
                        max_lines=25,
                        interactive=False,
                        autoscroll=True,
                        placeholder="TidalCycles patterns will appear here..."
                    )
                with gr.Column(scale=1):
                    executed_tasks_output = gr.Textbox(
                        label="Executed Tasks Log",
                        lines=25,
                        max_lines=25,
                        interactive=False,
                        autoscroll=True,
                        placeholder="Completed tasks will appear here..."
                    )
    
    # Event handlers
    def start_handler(task_text, test_mode, composer_model, director_model, director_interval):
        result = backend.start_system(task_text, test_mode, director_interval, composer_model, director_model)
        return result, gr.update(interactive=False), gr.update(interactive=True)
    
    def stop_handler():
        result = backend.stop_system()
        return result, gr.update(interactive=True), gr.update(interactive=False)
    
    start_btn.click(
        fn=start_handler,
        inputs=[task_input, test_mode_checkbox, composer_model_dropdown, director_model_dropdown, director_interval_slider],
        outputs=[status_text, start_btn, stop_btn]
    )
    
    stop_btn.click(
        fn=stop_handler,
        outputs=[status_text, start_btn, stop_btn]
    )
    
    # Auto-refresh song context and executed tasks every 0.5 seconds
    timer = gr.Timer(0.5)
    timer.tick(fn=backend.get_song_context, outputs=song_context_output)
    timer.tick(fn=backend.get_executed_tasks, outputs=executed_tasks_output)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, theme=gr.themes.Soft())
