class ArtifactHandler:
    """Handles conversion between Claude artifacts and Slack blocks"""
    
    ARTIFACT_PATTERN = re.compile(
        r'<antArtifact\s+identifier="([^"]+)"\s+type="([^"]+)"\s+(?:language="([^"]+)")?\s*title="([^"]+)">\s*(.*?)\s*</antArtifact>',
        re.DOTALL
    )
    
    @staticmethod
    def parse_artifacts(text):
        """Extract artifacts from Claude's response text"""
        artifacts = []
        cleaned_text = text
        
        for match in ArtifactHandler.ARTIFACT_PATTERN.finditer(text):
            identifier = match.group(1)
            type = match.group(2)
            language = match.group(3)
            title = match.group(4)
            content = match.group(5)
            
            artifacts.append({
                "identifier": identifier,
                "type": type,
                "language": language,
                "title": title,
                "content": content
            })
            
            # Remove the artifact tag from cleaned text
            cleaned_text = cleaned_text.replace(match.group(0), "")
            
        return cleaned_text.strip(), artifacts

    @staticmethod
    def convert_to_blocks(text, artifacts):
        """Convert Claude response and artifacts into Slack blocks"""
        blocks = []
        
        # Add main text if present
        if text:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": text}
            })
        
        # Process each artifact
        for artifact in artifacts:
            blocks.extend(ArtifactHandler.artifact_to_blocks(artifact))
            
        return blocks

    @staticmethod
    def artifact_to_blocks(artifact):
        """Convert a single artifact to Slack blocks"""
        blocks = []
        
        # Add title divider
        blocks.append({
            "type": "divider"
        })
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{artifact['title']}*"
            }
        })
        
        content = artifact['content']
        artifact_type = artifact['type']
        
        if artifact_type == "application/vnd.ant.code":
            # Code artifacts
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"```{artifact['language'] or ''}\n{content}\n```"
                }
            })
            
        elif artifact_type == "text/markdown":
            # Markdown artifacts
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": content
                }
            })
            
        elif artifact_type in ["text/html", "application/vnd.ant.react"]:
            # HTML and React artifacts as code blocks
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"```html\n{content}\n```"
                }
            })
            
        elif artifact_type == "image/svg+xml":
            # SVG artifacts as code blocks
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"```xml\n{content}\n```"
                }
            })
            
        elif artifact_type == "application/vnd.ant.mermaid":
            # Mermaid diagrams as code blocks
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"```mermaid\n{content}\n```"
                }
            })
            
        return blocks

    @staticmethod
    def reconstruct_artifacts(blocks):
        """Reconstruct artifacts from Slack blocks for context preservation"""
        messages = []
        current_artifact = None
        
        for block in blocks:
            if block["type"] == "section":
                text = block["text"]["text"]
                
                # Check if this is a code block
                code_match = re.match(r'```(\w*)\n(.*?)\n```', text, re.DOTALL)
                if code_match:
                    language = code_match.group(1)
                    content = code_match.group(2)
                    
                    # Determine artifact type based on language
                    if language == "html":
                        type = "text/html"
                    elif language == "xml":
                        type = "image/svg+xml"
                    elif language == "mermaid":
                        type = "application/vnd.ant.mermaid"
                    else:
                        type = "application/vnd.ant.code"
                    
                    # Create artifact tag
                    if current_artifact:
                        messages.append(
                            f'<antArtifact identifier="{current_artifact}" '
                            f'type="{type}" language="{language}" '
                            f'title="Code Block">{content}</antArtifact>'
                        )
                        current_artifact = None
                else:
                    # Regular text
                    messages.append(text)
                    
            elif block["type"] == "divider":
                # Start of new artifact section
                current_artifact = f"block-{len(messages)}"
                
        return "\n".join(messages)
