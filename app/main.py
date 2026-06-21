from flask import Flask, jsonify
app = Flask(__name__)

@app.route('/api/items/<int:item_id>', methods=['GET'])
def get_item(item_id):
    try:
        # assuming you have a function to get item by id
        item = get_item_by_id(item_id)
        if item is None:
            return jsonify({'error': 'Item not found'}), 404
        return jsonify(item)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)