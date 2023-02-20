def remove_comments(input_text):
    # define states for the state machine
    STATE_DEFAULT = 0
    STATE_SINGLE_COMMENT = 1
    STATE_MULTI_COMMENT = 2
    STATE_QUOTED = 3
    STATE_CHAR = 4
    
    # initialize state and output buffer
    state = STATE_DEFAULT
    output_text = ""
    
    # iterate over each character in the input text
    i = 0
    while i < len(input_text):
        c = input_text[i]
        
        # handle single-line comments
        if state == STATE_DEFAULT and c == '/' and i+1 < len(input_text) and input_text[i+1] == '/':
            state = STATE_SINGLE_COMMENT
            i += 1
        elif state == STATE_SINGLE_COMMENT and c == '\n':
            state = STATE_DEFAULT
        
        # handle multi-line comments
        elif state == STATE_DEFAULT and c == '/' and i+1 < len(input_text) and input_text[i+1] == '*':
            state = STATE_MULTI_COMMENT
            i += 1
        elif state == STATE_MULTI_COMMENT and c == '*' and i+1 < len(input_text) and input_text[i+1] == '/':
            state = STATE_DEFAULT
            i += 1
        
        # handle quoted text
        elif state == STATE_DEFAULT and (c == '"' or c == "'"):
            state = STATE_QUOTED
            output_text += c
        elif state == STATE_QUOTED and c == output_text[-1] and input_text[i-1] != '\\':
            state = STATE_DEFAULT
            output_text += c
        
        # handle character literals
        elif state == STATE_DEFAULT and c == "'":
            state = STATE_CHAR
            output_text += c
        elif state == STATE_CHAR and c == output_text[-1] and input_text[i-1] != '\\':
            state = STATE_DEFAULT
            output_text += c
        
        # handle non-comment text
        elif state == STATE_DEFAULT:
            output_text += c
        
        i += 1
    
    return output_text
