import io
from typing import Dict, Any

from ruamel.yaml import YAML

from qcodes.dataset.dependencies import InterDependencies


class RunDescriber:

    def __init__(self, interdeps: InterDependencies) -> None:
        self.interdeps = interdeps

    def serialize(self) -> Dict[str, Any]:
        """
        Serialize this object into a dictionary
        """
        ser = {}
        ser['Parameters'] = self.interdeps.serialize()
        return ser

    def output_yaml(self):
        """
        Output the run description as a yaml string
        """
        yaml = YAML()
        stream = io.StringIO()
        yaml.dump(self.serialize(), stream=stream)
        output = stream.getvalue()
        stream.close()
        return output
