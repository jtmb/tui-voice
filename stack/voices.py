"""Owned local Kokoro voices used by the tui-voice read-aloud worker."""
PRESETS = {
    'heart': dict(name='Heart', description='Warm and gentle · Original voice',
                  model='af_heart', lang='en-us', speed=1.15),
    'velvet': dict(name='Velvet', description='Warm, breathy female voice',
                   model='af_bella', lang='en-us', speed=.98),
    'english': dict(name='English Rose', description='British English · Clear and composed',
                    model='bf_emma', lang='en-gb', speed=1.02),
    'portuguese': dict(name='Dora', description='Brazilian Portuguese voice and pronunciation',
                       model='pf_dora', lang='pt-br', speed=1.02),
}
